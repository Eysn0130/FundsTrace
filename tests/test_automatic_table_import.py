"""Public, synthetic upgrade regressions independent of parser expectations."""
import csv
import io
import json
from decimal import Decimal
import zipfile

from openpyxl import Workbook
import pytest

from app import importer, table_archive, table_import
from app.services import imports
from test_feedback_import_api import complete


def preview(client, cid, content, filename="synthetic.csv"):
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": (filename, content)})
    assert response.status_code == 200, response.text
    return response.json()


def test_trial_labelled_counterparty_preserves_original_and_rejects_conflicting_account(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    content = ("交易时间,本方账号,对方账号,收付标志,交易金额,交易币种,交易对手信息,备注\n"
               "2046-04-15 08:01:00,SYN-OWN,000012300,出,5.11,CNY,户名：合成对手；账号：000012300；备注：号码 4488,原备注 9911\n"
               "2046-04-15 08:02:00,SYN-OWN,,出,5.22,CNY,合成未分列 12345678,原备注 9822\n"
               "2046-04-15 08:03:00,SYN-OWN,000012300,出,5.33,CNY,户名：合成对手；账号：000012301,原备注 9733\n").encode()
    cid = make_case("Synthetic labelled counterparty")
    view = preview(client, cid, content)
    assert view["table_summary"]["profile"] == table_import.BANK_COMPONENT_TRIAL_PROFILE
    assert view["table_summary"]["reason_counts"] == {"transaction_valid": 2, "conflicting_field": 1}
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 2
    base = f"/api/v1/cases/{cid}"
    flows = {row["amount"]: row for row in client.get(base + "/flows?granularity=observations").json()["items"]}
    assert (flows["5.11"]["payee_account"], flows["5.11"]["payee_name"], flows["5.11"]["summary"]) == (
        "000012300", "合成对手", "原备注 9911")
    assert flows["5.22"]["payee_account"] == "" and "5.33" not in flows
    records = client.get(base + f"/imports/{view['import_id']}/records").json()["items"]
    assert records[2]["flow_id"] is None
    [component] = [row for row in json.loads(records[0]["details"]["source_field_evidence"]) if row["field"] == "counterparty_text"]
    assert component["column_index"] == 7 and component["raw"].endswith("备注：号码 4488")
    assert client.get(base + f"/sources/{view['import_id']}/file").content == content


def test_automatic_csv_units_conflicts_full_disposition_and_exact_coverage(client, database, make_case):
    cid = make_case("Synthetic automatic units")
    content = ("交易时间,本方账号,交易对手账卡号,交易金额(分),交易金额(万元),收付标志,交易币种\n"
               "2026-01-01 10:00:00,000SYN-A,000SYN-B,100,0.0001,出,CNY\n"
               "2026-01-01 11:00:00,000SYN-A,000SYN-C,900,0.001,出,CNY\n"
               "2026-01-01 12:00:00,000SYN-A,000SYN-D,200,0.0002,未知,CNY\n"
               "2026-01-01 13:00:00,000SYN-A,000SYN-E,0,0,出,CNY\n").encode()
    view = preview(client, cid, content)
    assert view["automatic"] is True
    summary = view["table_summary"]
    assert (summary["record_total"], summary["accepted"], summary["unresolved"], summary["non_monetary"]) == (4, 1, 2, 1)
    assert summary["currency_totals"] == {"CNY": "1.00"}
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert (job["done"], job["total"], job["result"]["ok"]) == (4, 4, 1)
    source = view["import_id"]
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert len(flows) == 2 and {row["amount"] for row in flows} == {"0.00", "1.00"}
    assert all(row["payer_account"] == "000SYN-A" for row in flows)
    assert next(row for row in flows if row["amount"] == "0.00")["source_ledger"]["economic_amount"] == "0.00"
    report = client.get(f"/api/v1/cases/{cid}/sources/{source}/report").json()
    assert [r["disposition"] for r in report["items"]] == ["imported", "unresolved", "unresolved", "non_monetary"]
    assert [r["source_row"] for r in report["items"]] == [2, 3, 4, 5]
    assert client.get(f"/api/v1/cases/{cid}/sources/{source}/file").content == content
    coverage = client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]
    assert coverage["unresolved_records"] == 2 and coverage["non_monetary_records"] == 1
    assert coverage["financial_interpretation_complete"] is False
    other = preview(client, cid, content, "same-facts.csv")
    repeated = complete(client, cid, other)
    assert repeated["result"]["ok"] == 0 and repeated["result"]["rows_dup"] == 1
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"] == coverage
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid])[0]["n"] == 2
    assert client.delete(f"/api/batches/{job['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    database.x("update import_sessions set expires_at=current_timestamp-interval '1 day' where case_id=?", [cid])
    cleanup = client.post(f"/api/v1/cases/{cid}/imports/cleanup", json={"dry_run": False}).json()
    assert cleanup["protected"] == 2 and cleanup["deleted"] == 0
    assert client.get(f"/api/v1/cases/{cid}/sources/{source}/file").content == content


def test_entirely_unknown_table_completes_without_mapping_or_fabricated_flows(client, database, make_case):
    cid = make_case("Synthetic unsupported structure")
    view = preview(client, cid, b"unknown_a,unknown_b\nSYN-A,SYN-B\n")
    assert view["automatic"] and view["mapping"] == {}
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 0
    summary = job["result"]["table_summary"]
    assert summary["record_total"] == summary["unresolved"] == 2
    assert summary["currency_totals"] == {}
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid])[0]["n"] == 0
    report = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()
    assert len(report["items"]) == 2 and all(r["flow_id"] is None for r in report["items"])


def test_xlsx_all_sheets_keep_distinct_internal_and_original_positions(client, make_case):
    cid = make_case("Synthetic workbook")
    workbook = Workbook()
    for index, title in enumerate(("Synthetic first", "Synthetic second")):
        sheet = workbook.active if index == 0 else workbook.create_sheet()
        sheet.title = title
        sheet.append(["date", "payer_account", "payee_account", "amount(cents)"])
        sheet.append([f"2026-01-0{index+1}", "000SYN-A", f"000SYN-{index}", str((index + 1) * 100)])
    buffer = io.BytesIO()
    workbook.save(buffer)
    view = preview(client, cid, buffer.getvalue(), "synthetic.xlsx")
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 2
    assert job["result"]["table_summary"]["currency_totals"] == {"CNY": "3.00"}
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    assert [r["record_no"] for r in records] == [1, 2] and [r["source_row"] for r in records] == [2, 2]
    assert len({r["member_path"] for r in records}) == 2
    for record in records:
        source = client.get(f"/api/v1/cases/{cid}/flows/{record['flow_id']}/sources").json()["items"][0]
        assert source["row_kind"] == "table_record" and source["member_row"] == 2
        assert source["source_row"] == record["record_no"]
        assert source["member_path"] == record["member_path"]


def test_table_preview_is_pinned_and_mapping_cannot_change_without_repreview(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic pinned table")
    view = preview(client, cid, b"date,payer_account,payee_account,amount(cents)\n2026-01-01,SYN-A,SYN-B,100\n")
    changed = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/commit",
                          json={"mode": "A", "mapping": {**view["mapping"], "summary": "amount(cents)"}})
    assert changed.status_code == 409
    with monkeypatch.context() as modified:
        modified.setattr(imports, "TABLE_PROFILE", importer.PARSER_VERSION)
        job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["table_summary"]["profile"] == table_import.PROFILE
    assert database.q_raw("select amount from flows where case_id=?", [cid])[0]["amount"] == 1


def test_document_number_columns_cannot_map_to_account_or_card_in_public_import(client, database, make_case):
    cid = make_case("Synthetic document-number mapping")
    content = ("交易时间,本端账号,本端卡号,证件号,证件号,本端身份证件号码,证件编号,对方账号,交易金额,收付标志,交易币种\n"
               "2026-01-01 10:00:00,000SYN-A,SYN-CARD,SYN-ID1,SYN-ID2,SYN-ID3,SYN-ID4,SYN-B,100.00,出,CNY\n").encode()
    view = preview(client, cid, content)
    assert view["table_summary"]["accepted"] == 0
    column = "证件号 [列4]"
    assert column in view["columns"]
    path = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    for field in ("self_account", "self_card", "payer_account", "payee_account", "counter_account", "counter_card"):
        body = {"mode": "B", "mapping": {**view["mapping"], field: column}}
        response = client.post(path + "/preview", json=body)
        assert response.status_code == 422, response.text
        assert "证件" in response.text
    for header in ("本端身份证件号码", "证件编号"):
        response = client.post(path + "/preview", json={"mode": "B", "mapping": {**view["mapping"], "self_account": header}})
        assert response.status_code == 422, response.text

    # A session left ready by the previous version must not bypass the new
    # write boundary by submitting the same unsafe mapping.
    mapping = {**view["mapping"], "self_account": column}
    database.x("update import_sessions set mapping_json=? where import_id=?",
               [imports._json(mapping), view["import_id"]])
    blocked = client.post(path + "/commit", json={"mode": "B", "mapping": mapping})
    assert blocked.status_code == 422, blocked.text
    assert "证件" in blocked.text
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0

    corrected = client.post(path + "/preview",
                            json={"mode": "B", "mapping": {**view["mapping"], "self_account": "本端账号"}})
    assert corrected.status_code == 200, corrected.text
    assert complete(client, cid, corrected.json())["result"]["ok"] == 1


@pytest.mark.parametrize("balance_header", ["余额", "本笔交易后账面余额", "账面余额", "可用余额", "对手余额"])
def test_trial_repreview_cannot_fill_a_missing_amount_from_an_explicit_balance(
        client, database, make_case, monkeypatch, balance_header):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Synthetic amount versus balance")
    content = (f"交易时间,本方卡号,对方账号,交易金额,{balance_header},收付标志,币种\n"
               "2038-08-01 12:34:56,00012345,00023456,,105.34,入,CNY\n"
               "2038-08-02 12:34:56,00012345,00023456,9.00,9.00,入,CNY\n").encode()
    view = preview(client, cid, content)
    assert view["table_summary"]["reason_counts"] == {"missing_amount": 1, "transaction_valid": 1}
    base = f"/api/v1/cases/{cid}"
    path = base + f"/imports/{view['import_id']}"
    bad = {"mode": "B", "mapping": {**view["mapping"], "amount": balance_header}}
    rejection = client.post(path + "/preview", json=bad)
    assert rejection.status_code == 422, rejection.text
    assert rejection.json()["error"]["code"] == "invalid_mapping"
    assert client.post(path + "/commit", json=bad).status_code == 409
    assert client.get(base + "/flows").json()["total"] == 0
    good = client.post(path + "/preview", json={"mode": "B", "mapping": view["mapping"]})
    assert good.status_code == 200, good.text
    assert complete(client, cid, good.json())["result"]["ok"] == 1
    with database._lock:
        database._con.close()
        database._con = None
    [flow] = client.get(base + "/flows").json()["items"]
    assert (flow["amount"], flow["payer_account"], flow["payee_account"]) == ("9.00", "00023456", "00012345")
    records = client.get(path + "/records").json()["items"]
    assert [(row["source_row"], row["reason_code"]) for row in records] == [(2, "missing_amount"), (3, "transaction_valid")]
    assert client.get(base + f"/sources/{view['import_id']}/file").content == content


def test_trial_repreview_rejects_explicit_opposite_identity_roles_and_allows_an_unknown_heading(
        client, database, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Synthetic counterparty name and account")
    content = ("交易时间,本方卡号,对方账号,对方户名,交易金额,收付标志,币种,附加内容\n"
               "2038-08-01 12:34:56,00012345,00023456,SYN Peer,8.34,入,CNY,SYN extra name\n").encode()
    view = preview(client, cid, content)
    base = f"/api/v1/cases/{cid}"
    path = base + f"/imports/{view['import_id']}"
    for wrong_role in ({"counter_name": "对方账号"}, {"counter_account": "对方户名"}, {"counter_card": "对方户名"}):
        bad = {"mode": "B", "mapping": {**view["mapping"], **wrong_role}}
        rejection = client.post(path + "/preview", json=bad)
        assert rejection.status_code == 422, rejection.text
        assert rejection.json()["error"]["code"] == "invalid_mapping"
        assert client.post(path + "/commit", json=bad).status_code == 409
    assert client.get(base + "/flows").json()["total"] == 0
    adhoc = client.post(path + "/preview", json={"mode": "B", "mapping": {**view["mapping"], "counter_name": "附加内容"}})
    assert adhoc.status_code == 200 and adhoc.json()["preview"][0]["payer_name"] == "SYN extra name"
    good = client.post(path + "/preview", json={"mode": "B", "mapping": view["mapping"]})
    assert good.status_code == 200, good.text
    assert complete(client, cid, good.json())["result"]["ok"] == 1
    with database._lock:
        database._con.close()
        database._con = None
    [flow] = client.get(base + "/flows").json()["items"]
    assert (flow["payer_name"], flow["payer_account"], flow["payee_account"], flow["amount"]) == (
        "SYN Peer", "00023456", "00012345", "8.34")


@pytest.mark.parametrize(("field", "column", "balance"), [
    ("balance", "交易金额", ""),
    ("balance", "交易金额", "8.34"),
    ("balance", "可用余额", ""),
    ("balance", "对手余额", ""),
    ("counter_name", "收付标志", ""),
])
def test_trial_repreview_rejects_explicit_amount_balance_and_direction_conflicts(
        client, database, make_case, monkeypatch, field, column, balance):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Synthetic balance or counterparty field conflict")
    content = ("交易时间,本方卡号,对方账号,对方户名,交易金额,交易后账面余额,收付标志,币种,可用余额,对手余额\n"
               f"2038-08-01 12:34:56,00012345,00023456,SYN Peer,8.34,{balance},入,CNY,125.52,87.60\n").encode()
    view = preview(client, cid, content)
    [original] = view["preview"]
    assert (original["amount"], original["balance"], original["payer_name"]) == ("8.34", balance or None, "SYN Peer")
    base = f"/api/v1/cases/{cid}"
    path = base + f"/imports/{view['import_id']}"
    bad = {"mode": view["mode"], "mapping": {**view["mapping"], field: column}}
    rejected = client.post(path + "/preview", json=bad)
    assert rejected.status_code == 422, rejected.status_code
    assert rejected.json()["error"]["code"] == "invalid_mapping"
    assert client.post(path + "/commit", json=bad).status_code == 409
    assert client.get(base + "/flows").json()["total"] == 0
    correct = client.post(path + "/preview", json={"mode": view["mode"], "mapping": view["mapping"]})
    assert correct.status_code == 200, correct.text
    assert complete(client, cid, correct.json())["result"]["ok"] == 1
    with database._lock:
        database._con.close()
        database._con = None
    [saved] = client.get(base + "/flows").json()["items"]
    fields = ("payer_name", "payer_account", "payee_account", "amount", "balance")
    assert {key: saved[key] for key in fields} == {key: original[key] for key in fields}
    assert client.get(base + f"/sources/{view['import_id']}/file").content == content


@pytest.mark.parametrize(("field", "heading"), [("trade_time", "开户日期"), ("time_part", "账号开户时间")])
def test_trial_opening_clock_cannot_fill_a_transaction_clock(client, make_case, monkeypatch, field, heading):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case()
    raw = (f"交易时间,本方账号,对方账号,交易金额,收付标志,交易币种,{heading}\n"
           ",000123,000234,10.20,出,CNY,2040-08-03\n").encode()
    view = preview(client, cid, raw)
    assert view["table_summary"]["reason_counts"] == {"missing_time": 1}
    base = f"/api/v1/cases/{cid}"
    route = base + f"/imports/{view['import_id']}"
    wrong = {"mode": view["mode"], "mapping": {**view["mapping"], field: heading}}
    denied = client.post(route + "/preview", json=wrong)
    assert (denied.status_code, denied.json().get("error", {}).get("code")) == (422, "invalid_mapping")
    assert client.post(route + "/commit", json=wrong).status_code == 409
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


def test_opening_clock_guard_keeps_ordinary_and_already_frozen_mappings(client, make_case, monkeypatch):
    raw = ("交易时间,本方账号,对方账号,交易金额,收付标志,交易币种,开户日期\n"
           ",000123,000234,10.20,出,CNY,2040-08-03\n").encode()

    def remap(cid, view):
        route = f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview"
        body = {"mode": view["mode"], "mapping": {**view["mapping"], "trade_time": "开户日期"}}
        return client.post(route, json=body).raise_for_status().json()

    default = make_case("Synthetic default opening clock profile")
    assert remap(default, preview(client, default, raw))["stats"]["accepted"] == 1
    pinned = make_case("Synthetic frozen opening clock mapping")
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    with monkeypatch.context() as old:
        old.setattr(table_import, "mapping_contradicts_source_roles", lambda *_: False)
        earlier = remap(pinned, preview(client, pinned, raw))
    assert earlier["stats"]["accepted"] == 1
    restored = remap(pinned, earlier)
    assert restored["preview"] == earlier["preview"]
    assert complete(client, pinned, restored)["stage"] == "done"


@pytest.mark.parametrize(("field", "column", "output_field", "expected"), [
    ("counter_name", "对方账号", "payer_name", "00023456"),
    ("balance", "可用余额", "balance", "125.52"),
])
def test_pinned_ready_role_mapping_replays_and_production_scope_is_unchanged(
        client, database, make_case, monkeypatch, field, column, output_field, expected):
    content = ("交易时间,本方账号,对方账号,对方户名,交易金额,收付标志,币种,可用余额\n"
               "2038-08-01 12:34:56,00012345,00023456,SYN Peer,8.34,入,CNY,125.52\n").encode()

    def change(cid, view):
        path = f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview"
        return client.post(path, json={"mode": "B", "mapping": {**view["mapping"], field: column}})

    ordinary = make_case("Synthetic historic ordinary mapping")
    result = change(ordinary, preview(client, ordinary, content))
    assert result.status_code == 200 and result.json()["preview"][0][output_field] == expected, result.text

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    pinned = make_case("Synthetic historic trial mapping")
    with monkeypatch.context() as old:
        old.setattr(table_import, "mapping_contradicts_source_roles", lambda *_: False, raising=False)
        saved = change(pinned, preview(client, pinned, content))
    assert saved.status_code == 200, saved.text
    view = saved.json()
    database.x("update import_sessions set expires_at='2020-01-01' where import_id=?", [view["import_id"]])
    with database._lock:
        database._con.close()
        database._con = None
    replayed = change(pinned, view)
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["table_summary"]["profile"] == view["table_summary"]["profile"]
    assert replayed.json()["preview"] == view["preview"]
    assert complete(client, pinned, replayed.json())["result"]["ok"] == 1


@pytest.mark.parametrize("heading", ["进出标志", "进出标识"])
def test_trial_template_direction_uses_existing_enums_on_the_registered_source(
        client, database, make_case, monkeypatch, heading):
    def original(label, directions):
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["交易时间", "本方卡号", "对方账号", "交易金额", label, "币种"])
        writer.writerows([f"2038-08-0{index} 12:34:56", "00012345", "00023456", amount, sign, "CNY"]
                         for index, (sign, amount) in enumerate(directions, 1))
        return output.getvalue().encode()

    raw = original(heading, [("进", "8.34"), ("出", "2.15")])
    ordinary = make_case("Synthetic prior direction template")
    old = preview(client, ordinary, raw)
    assert old["table_summary"]["profile"] == table_import.VOUCHER_REFERENCE_PROFILE
    assert old["table_summary"]["reason_counts"] == {"missing_direction": 2}

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Synthetic declared direction template")
    view = preview(client, cid, raw)
    summary = view["table_summary"]
    assert summary["profile"] == table_import.BANK_TEMPLATE_TRIAL_PROFILE
    assert summary["reason_counts"] == {"transaction_valid": 2}
    assert view["mapping"]["direction"] == heading and view["automatic"] and not view["layout_model_trial"]
    assert complete(client, cid, view)["result"]["ok"] == 2
    assert complete(client, ordinary, old)["result"]["ok"] == 0
    expected = {("00023456", "00012345", "8.34"), ("00012345", "00023456", "2.15")}
    with database._lock:
        database._con.close()
        database._con = None
    base = f"/api/v1/cases/{cid}"
    assert client.get(base + f"/sources/{view['import_id']}/file").content == raw
    flows = client.get(base + "/flows").json()["items"]
    assert {(row["payer_account"], row["payee_account"], row["amount"]) for row in flows} == expected
    archive = client.get(f"/api/cases/{cid}/export").content
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", archive)})
    assert restored.status_code == 200, restored.text
    saved = client.get(f"/api/v1/cases/{restored.json()['case_id']}/flows").json()["items"]
    assert {(row["payer_account"], row["payee_account"], row["amount"]) for row in saved} == expected

    unknown = make_case("Synthetic undeclared direction values")
    bad = preview(client, unknown, original(heading, [("02", "8.34"), ("+", "2.15"), ("-", "3.51")]))
    assert bad["table_summary"]["profile"] == table_import.BANK_TEMPLATE_TRIAL_PROFILE
    assert bad["table_summary"]["reason_counts"] == {"invalid_direction": 3}
    assert bad["layout_model_trial"] and bad["table_summary"]["accepted"] == 0
    near = preview(client, unknown, original("进出类型", [("出", "2.15")]))
    assert near["table_summary"]["profile"] == table_import.VOUCHER_REFERENCE_PROFILE
    assert near["table_summary"]["reason_counts"] == {"missing_direction": 1}


@pytest.mark.parametrize("layout_owner", [False, True])
def test_trial_book_and_available_columns_keep_only_book_balance_as_a_row_observation(
        client, database, make_case, monkeypatch, layout_owner):
    from test_api_v1 import poll
    from test_import_trial_rules_first import commit_body

    if layout_owner:
        opening = ("字段口径：甲账户是逐笔交易本方账号；乙账户是归集备用本方账号。\n"
                   "交易时间,甲账户,乙账户,对方账号,借方发生额,贷方发生额,账面余额,可用余额,余额单位,币种\n")
        entries = ("2038-08-01 12:34:56,00012345,00012345,00023456,0,8.34,10534,4023,分,CNY\n"
                   "2038-08-02 12:34:56,00012345,00098765,00034567,2.15,0,10319,3808,分,CNY\n")
    else:
        opening = "交易日期,交易时间,本方账号,对方账号,借方发生额,贷方发生额,账面余额,可用余额,余额单位,币种\n"
        entries = ("2038-08-01,12:34:56,00012345,00023456,0,8.34,10534,4023,分,CNY\n"
                   "2038-08-02,12:34:56,00012345,00034567,2.15,0,10319,3808,分,CNY\n")
    raw = (opening + entries).encode()
    old_case = make_case("Synthetic original balance profile")
    old = preview(client, old_case, raw)
    assert old["table_summary"]["profile"] == table_import.VOUCHER_REFERENCE_PROFILE
    assert "balance" not in old["mapping"]
    assert all(row["balance"] is None for row in old["preview"])

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Synthetic observed book balance without time authority")
    view = preview(client, cid, raw)
    assert (view["table_summary"]["profile"], view["mapping"].get("balance")) == (
        table_import.BANK_TEMPLATE_TRIAL_PROFILE, "账面余额")
    assert view["automatic"] and not view["layout_model_trial"]
    assert view["layout_applied"] is layout_owner
    base = f"/api/v1/cases/{cid}"
    path = base + f"/imports/{view['import_id']}"
    response = client.post(path + "/commit", json=commit_body(view))
    assert response.status_code == 200, response.text
    job = poll(client, cid, view["import_id"])
    assert job["stage"] == "done" and job["result"]["ok"] == 2
    with database._lock:
        database._con.close()
        database._con = None
    facts = {("00023456", "00012345", "8.34", "105.34"), ("00012345", "00034567", "2.15", "103.19")}
    keys = ("payer_account", "payee_account", "amount", "balance")
    flows = client.get(base + "/flows").json()["items"]
    assert {tuple(row[key] for key in keys) for row in flows} == facts
    review = client.get(path + "/review", params={"member_path": "table.csv", "view": "accepted"}).json()
    assert [row["details"]["balance_basis"] for row in review["items"]] == ["no_same_entry_pair"] * 2
    assert [next(cell["value"] for cell in row["original"] if cell["header"] == "可用余额")
            for row in review["items"]] == ["4023", "3808"]
    assert client.get(base + f"/sources/{view['import_id']}/file").content == raw
    archive = client.get(f"/api/cases/{cid}/export").content
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", archive)})
    assert restored.status_code == 200, restored.text
    saved = client.get(f"/api/v1/cases/{restored.json()['case_id']}/flows").json()["items"]
    assert {tuple(row[key] for key in keys) for row in saved} == facts


@pytest.mark.parametrize(("titles", "values", "expected"), [
    ("可用余额", "4023", None),
    ("账面余额,账面余额,可用余额", "10534,10101,4023", None),
    ("账面余额,可用余额,可用余额", "10534,4023,8112", None),
    ("对手账面余额,可用余额", "10534,4023", None),
    ("账面余额,可用余额,本笔交易后账面余额", "10534,4023,91.27", "91.27"),
])
def test_trial_book_balance_does_not_override_ambiguous_or_attributed_columns(
        client, make_case, monkeypatch, titles, values, expected):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    raw = (f"交易时间,本方卡号,对方账号,交易金额,收付标志,币种,{titles}\n"
           f"2038-08-01 12:34:56,00012345,00023456,8.34,入,CNY,{values}\n").encode()
    view = preview(client, case, raw)
    assert view["table_summary"]["profile"] == table_import.VOUCHER_REFERENCE_PROFILE
    [flow] = view["preview"]
    assert flow["balance"] == expected


def test_trial_book_balance_without_a_posting_time_cannot_supply_direction(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    raw = ("交易时间,本方卡号,对方账号,交易金额,币种,交易前账面余额,账面余额,可用余额\n"
           "2038-08-01 12:34:56,00012345,00023456,8.34,CNY,97.00,105.34,40.23\n").encode()
    view = preview(client, make_case(), raw)
    assert view["table_summary"]["profile"] == table_import.BANK_TEMPLATE_TRIAL_PROFILE
    assert view["table_summary"]["reason_counts"] == {"missing_direction": 1}


def test_trial_book_balance_respects_each_region_and_the_pinned_archive_leaf_rule(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    header = "交易时间,本方卡号,对方账号,交易金额,收付标志,币种"
    valid = (header + ",账面余额,可用余额\n"
             "2038-08-01 12:34:56,00012345,00023456,8.34,入,CNY,105.34,40.23\n")
    different_header = (header + ",对手账面余额,可用余额\n"
                        "2038-08-02 12:34:56,00012345,00034567,9.15,入,CNY,200.35,60.12\n")
    cid = make_case("Synthetic independent balance regions")
    view = preview(client, cid, (valid + different_header).encode())
    assert view["table_summary"]["profile"] == table_import.BANK_TEMPLATE_TRIAL_PROFILE
    assert [row["balance"] for row in view["preview"]] == ["105.34", None]
    assert complete(client, cid, view)["result"]["ok"] == 2

    archive_case = make_case("Synthetic acknowledged archive parser")
    response = client.post(f"/api/v1/cases/{archive_case}/imports/directory/preview", files=[
        ("files", ("observations/book.csv", valid.encode())),
        ("files", ("observations/recognized.csv", coded_bank_csv([
            ("D:借", "-4.25", "95.88", "人民币", "10", "", "SYN-PEER"),
        ]))),
    ])
    assert response.status_code == 200, response.text
    prior = response.json()
    assert prior["table_summary"]["profile"] == table_archive.TRIAL_MEMBER_PROFILE
    [book] = [row for row in prior["preview"] if row["amount"] == "8.34"]
    assert book["balance"] is None
    assert complete(client, archive_case, prior)["result"]["ok"] == 2
    flows = client.get(f"/api/v1/cases/{archive_case}/flows?granularity=observations").json()["items"]
    [replayed] = [row for row in flows if row["amount"] == "8.34"]
    assert replayed["balance"] is None


def test_manual_mapping_provenance_is_preserved_through_public_commit(client, make_case):
    cid = make_case("Synthetic manual mapping provenance")
    content = ("交易时间,本端账号,开户名称,对方账号,交易金额,收付标志,交易币种\n"
               "2026-01-01 10:00:00,000SYN-A,合成户名,SYN-B,100.00,出,CNY\n").encode()
    view = preview(client, cid, content)
    assert view["table_summary"]["accepted"] == 0
    path = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    mapping = {**view["mapping"], "self_account": "本端账号", "self_name": "开户名称"}
    recheck = client.post(path + "/preview", json={"mode": "B", "mapping": mapping})
    assert recheck.status_code == 200, recheck.text
    ready = recheck.json()
    assert ready["table_summary"]["accepted"] == 1

    def basis(summary):
        return {(entry["field"], entry["column"]): entry["basis"] for entry in summary["mapping_evidence"]}

    expected = {("self_account", "本端账号"): "manual_override",
                ("self_name", "开户名称"): "manual_override",
                ("counter_account", "对方账号"): "exact_header_alias",
                ("amount", "交易金额"): "exact_header_alias"}
    for key, value in expected.items():
        assert basis(ready["table_summary"])[key] == value
    again = client.post(path + "/preview", json={"mode": ready["mode"], "mapping": ready["mapping"]})
    assert again.status_code == 200, again.text
    assert basis(again.json()["table_summary"]) == basis(ready["table_summary"])
    job = complete(client, cid, again.json())
    assert job["stage"] == "done", job
    assert basis(job["result"]["table_summary"]) == basis(ready["table_summary"])
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == content


def test_historical_unpinned_session_keeps_v4_direction_contract(client, database, make_case):
    cid = make_case("Synthetic historical session")
    content = ("交易时间,本方账号,对方账号,交易金额,收入金额,支出金额,收付标志\n"
               "2026-01-01 10:00:00,SYN-A,SYN-B,1.01,0,9.99,收入\n").encode()
    view = preview(client, cid, content)
    assert view["table_summary"]["unresolved"] == 1
    # Recreate a historical ready session without the new internal version pin.
    database.x("update import_sessions set request_json=null where import_id=?", [view["import_id"]])
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    assert database.q_raw("select parser_version from import_sources where source_id=?", [view["import_id"]])[0]["parser_version"] == importer.PARSER_VERSION
    assert database.q_raw("select amount,payee_account from flows where case_id=?", [cid])[0] == {"amount": Decimal("1.01"), "payee_account": "SYN-A"}


def test_table_journal_failure_rolls_back_all_new_facts_then_retry(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic automatic rollback")
    view = preview(client, cid, b"date,payer_account,payee_account,amount\n2026-01-01,SYN-A,SYN-B,1.00\n")
    original = database.insert_dataframe

    def fail_after_journal(table, frame, columns):
        result = original(table, frame, columns)
        if table == "import_records":
            raise RuntimeError("Synthetic rollback")
        return result

    with monkeypatch.context() as failure:
        failure.setattr(database, "insert_dataframe", fail_after_journal)
        assert complete(client, cid, view)["stage"] == "error"
    for table in ("flows", "batches", "flow_sources", "import_sources", "import_members", "import_records"):
        assert database.q_raw(f"select count(*) n from {table} where case_id=?", [cid])[0]["n"] == 0
    assert complete(client, cid, view)["stage"] == "done"
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid])[0]["n"] == 1


def test_adding_nested_container_cannot_downgrade_old_feedback_reversal_policy(client, make_case):
    from test_cancellation_import_lifecycle import paired_source

    cid = make_case("Synthetic profile downgrade denial")
    leaf = io.BytesIO()
    with zipfile.ZipFile(leaf, "w") as package:
        package.writestr("unknown.csv", "unknown_a,unknown_b\nSYN-X,SYN-Y\n")
    mixed = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(paired_source())) as original, zipfile.ZipFile(mixed, "w") as package:
        for info in original.infolist():
            package.writestr(info.filename, original.read(info))
        package.writestr("extra.zip", leaf.getvalue())
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("synthetic.zip", mixed.getvalue())})
    # Old envelope replay retains its downgrade rejection. New native28
    # observations can retain the full source and prove the same cancellation.
    with pytest.raises(table_import.TableImportError, match="原规则"):
        table_archive.parse(io.BytesIO(mixed.getvalue()), profile=table_archive.AUXILIARY_SCOPE_PROFILE)
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["table_summary"]["cancelled"] == 2
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    rows = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()["items"]
    assert [row["amount"] for row in rows if not row["source_ledger"]] == ["1.00"]
    assert sorted(row["amount"] for row in rows if row["source_ledger"]) == ["-10.00", "10.00"]
    records = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()["items"]
    assert sum(item["disposition"] == "cancelled" for item in records) == 2
    assert any(item["member_path"] == "extra.zip/unknown.csv" and item["disposition"] == "unresolved" for item in records)


def test_feedback_envelope_keeps_distinct_source_events_but_normal_csv_retains_fact_dedup(client, make_case):
    content = (b"date,payer_account,payee_account,amount\n"
               b"2026-01-01,SYN-A,SYN-B,1.00\n2026-01-01,SYN-A,SYN-B,1.00\n")
    leaf = io.BytesIO()
    with zipfile.ZipFile(leaf, "w") as package:
        package.writestr("transactions.csv", content)
    envelope = io.BytesIO()
    with zipfile.ZipFile(envelope, "w") as package:
        package.writestr("nested.zip", leaf.getvalue())
    cid = make_case("Synthetic physical bank events")
    first = complete(client, cid, preview(client, cid, envelope.getvalue(), "synthetic.zip"))
    assert first["stage"] == "done" and first["result"]["ok"] == 2
    repeated = complete(client, cid, preview(client, cid, envelope.getvalue(), "renamed.zip"))
    assert repeated["result"]["ok"] == 0 and repeated["result"]["rows_dup"] == 2
    normal = make_case("Synthetic original table dedup")
    ordinary = complete(client, normal, preview(client, normal, content))
    assert ordinary["result"]["ok"] == 1 and ordinary["result"]["rows_dup"] == 1


@pytest.mark.parametrize("prior", ["ordinary", "envelope", "other_csv"])
def test_same_physical_csv_in_envelope_promotes_folded_rows_without_double_counting(client, make_case, prior):
    content = (b"date,payer_account,payee_account,amount\n"
               b"2026-01-01,SYN-A,SYN-B,1.00\n2026-01-01,SYN-A,SYN-B,1.00\n")
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as package:
        package.writestr("unknown.csv", b"unknown_a,unknown_b\nSYN-X,SYN-Y\n")
    envelope = io.BytesIO()
    with zipfile.ZipFile(envelope, "w") as package:
        package.writestr("table.csv", content)
        package.writestr("nested.zip", nested.getvalue())
    cid = make_case("Synthetic physical-source promotion")
    if prior == "other_csv":
        other = content.replace(b",amount\n", b",amount(yuan)\n")
        assert complete(client, cid, preview(client, cid, other))["result"]["ok"] == 1
    if prior != "envelope":
        ordinary = complete(client, cid, preview(client, cid, content))
        assert ordinary["result"]["ok"] == (0 if prior == "other_csv" else 1)
        assert ordinary["result"]["rows_dup"] == (2 if prior == "other_csv" else 1)
    view = preview(client, cid, envelope.getvalue(), "synthetic.zip")
    result = complete(client, cid, view)
    assert result["stage"] == "done"
    assert result["result"]["ok"] == (2 if prior == "envelope" else 1)
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    physical = [row for row in records if row["member_path"] == "table.csv"]
    assert len(physical) == 2 and len({row["flow_id"] for row in physical}) == 2
    for data, name in ((content, "synthetic.csv"), (envelope.getvalue(), "repeated.zip")):
        repeated = complete(client, cid, preview(client, cid, data, name))
        assert repeated["stage"] == "done" and repeated["result"]["ok"] == 0
        assert repeated["result"]["rows_dup"] == 2
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert len(flows) == 2 and sum(Decimal(row["amount"]) for row in flows) == Decimal("2.00")


@pytest.mark.parametrize("archive_profile", ["funds-trace-table-archive-v3", "funds-trace-table-archive-v4", "funds-trace-table-archive-v15", "funds-trace-table-archive-v18"],
                         ids=["frozen-v3", "frozen-v4", "frozen-v15", "default-v18"])
def test_envelope_replay_does_not_reuse_different_facts_from_manual_table_mapping(
        client, database, make_case, monkeypatch, archive_profile):
    import json
    from app import case_packages
    from test_archive_profile_upgrade import frozen_preview
    from test_feedback_package import _members
    from test_table_contract_lifecycle import facts
    from test_table_currency_region_lifecycle import receipt, source_snapshot
    from test_table_mapping_coverage import coverage

    content = b"date,payer_account,payee_account,amount\n2026-01-01,SYN-A,SYN-B,1.00\n"
    cid = make_case("Synthetic changed interpretation")
    view = preview(client, cid, content)
    assert view["table_summary"]["profile"] == table_import.VOUCHER_REFERENCE_PROFILE
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={
        "mode": "A", "mapping": {**view["mapping"], "payer_account": "payee_account", "payee_account": "payer_account"}})
    assert response.status_code == 200, response.text
    first = complete(client, cid, response.json())
    assert first["stage"] == "done" and first["result"]["ok"] == 1
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    assert len(before) == 1
    assert (before[0]["payer_account"], before[0]["payee_account"]) == ("SYN-B", "SYN-A")
    frozen = source_snapshot(database, cid, view["import_id"])
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w"):
        pass
    envelope = io.BytesIO()
    with zipfile.ZipFile(envelope, "w") as package:
        package.writestr("table.csv", content)
        package.writestr("nested.zip", nested.getvalue())
    def archive_preview(case_id, filename):
        if archive_profile != table_archive.PROFILE:
            current = frozen_preview(client, case_id, envelope.getvalue(), monkeypatch, archive_profile)
        else:
            current = preview(client, case_id, envelope.getvalue(), filename)
        assert current["table_summary"]["profile"] == archive_profile
        return current

    automatic = archive_preview(cid, "synthetic.zip")
    result = complete(client, cid, automatic)
    assert result["stage"] == "done", result
    records = client.get(f"/api/v1/cases/{cid}/imports/{automatic['import_id']}/records").json()["items"]
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert len(records) == 1 and records[0]["source_row"] == 2
    if archive_profile == "funds-trace-table-archive-v3":
        assert (result["result"]["ok"], result["result"]["rows_dup"]) == (1, 0)
        flow_id = records[0]["flow_id"]
        actual = next(row for row in flows if row["id"] == str(flow_id))
        assert actual["payer_account"] == "SYN-A" and actual["payee_account"] == "SYN-B"
        assert actual["id"] != str(before[0]["id"])
    else:
        assert (result["result"]["ok"], result["result"]["rows_dup"]) == (0, 0)
        assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before
        assert (records[0]["disposition"], records[0]["reason_code"], records[0]["flow_id"]) == (
            "unresolved", "conflicting_field", None)
        assert records[0]["details"]["counterparty_basis"] == table_import.ARCHIVE_HISTORY_CONFLICT_BASIS
        assert records[0]["details"]["financial_effect"] == "undetermined"
        assert "conflicting_field" in records[0]["details"]["validation_issues"].split(",")
        if archive_profile == table_archive.REFERENCE_ROLE_PROFILE:
            from app import table_cancellation_links
            # This real public reimport produced a competing interpretation.
            # The shared cancellation reader must retain the same blocking
            # assessment instead of discharging it as an ordinary negative.
            blocking = database.q_raw(f"""select ({table_cancellation_links.conflict_sql('s', 'r')}) blocked
                from import_records r join import_sources s on s.case_id=r.case_id and s.source_id=r.source_id
                where r.case_id=? and r.source_id=?""", [cid, automatic['import_id']])
            assert blocking == [{"blocked": True}]
        assert coverage(client, cid)["unresolved_records"] > 0
        assert coverage(client, cid)["financial_interpretation_complete"] is False
    repeated_view = archive_preview(cid, "repeat.zip")
    repeated = complete(client, cid, repeated_view)
    assert repeated["stage"] == "done" and repeated["result"]["ok"] == 0
    assert repeated["result"]["rows_dup"] == (1 if archive_profile == "funds-trace-table-archive-v3" else 0)
    if archive_profile != "funds-trace-table-archive-v3":
        repeated_records = client.get(
            f"/api/v1/cases/{cid}/imports/{repeated_view['import_id']}/records").json()["items"]
        assert (repeated_records[0]["disposition"], repeated_records[0]["reason_code"], repeated_records[0]["flow_id"]) == (
            "unresolved", "conflicting_field", None)
        assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before
        assert coverage(client, cid)["unresolved_records"] > 0
        assert coverage(client, cid)["financial_interpretation_complete"] is False
    assert database.q_raw("select * from flows where case_id=? and id=?", [cid, before[0]["id"]]) == before
    assert source_snapshot(database, cid, view["import_id"]) == frozen
    assert receipt(client, cid, view["import_id"]) == first["result"]
    archive = case_packages.export_package(cid)[0]
    assert json.loads(_members(archive)["meta.json"])["format"] == 6
    restored = case_packages.import_package(archive)["case_id"]
    if archive_profile == "funds-trace-table-archive-v3":
        assert len(client.get(f"/api/v1/cases/{restored}/flows").json()["items"]) == 2
    else:
        assert len(client.get(f"/api/v1/cases/{restored}/flows").json()["items"]) == 1
        assert facts(database, restored) == facts(database, cid)
        assert coverage(client, restored) == coverage(client, cid)
        restored_view = archive_preview(restored, "restored-repeat.zip")
        restored_job = complete(client, restored, restored_view)
        assert restored_job["stage"] == "done"
        assert (restored_job["result"]["ok"], restored_job["result"]["rows_dup"]) == (0, 0)
        restored_records = client.get(
            f"/api/v1/cases/{restored}/imports/{restored_view['import_id']}/records").json()["items"]
        assert (restored_records[0]["disposition"], restored_records[0]["reason_code"], restored_records[0]["flow_id"]) == (
            "unresolved", "conflicting_field", None)
        assert facts(database, restored) == facts(database, cid)
        assert coverage(client, restored)["unresolved_records"] > 0
        assert coverage(client, restored)["financial_interpretation_complete"] is False


@pytest.mark.parametrize("malformation", ["path", "depth", "duplicate"])
def test_new_envelope_rejects_unsafe_structure_before_writes(client, database, make_case, malformation):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as package:
        package.writestr("table.csv", b"date,payer_account,payee_account,amount\n2026-01-01,SYN-A,SYN-B,1.00\n")
    data = content.getvalue()
    depth = table_archive.MAX_DEPTH + 2 if malformation == "depth" else 1
    for number in range(depth):
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as package:
            package.writestr("../nested.zip" if malformation == "path" else f"nested{number}.zip", data)
            if malformation == "duplicate":
                with pytest.warns(UserWarning, match="Duplicate name"):
                    package.writestr(f"nested{number}.zip", data)
        data = content.getvalue()
    cid = make_case("Synthetic archive bounds")
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("synthetic.zip", data)})
    assert response.status_code == 400
    assert database.q_raw("select count(*) n from import_sessions where case_id=?", [cid])[0]["n"] == 0


@pytest.mark.parametrize("declared_event", [False, True])
def test_repreview_can_separate_voucher_reference_and_commit_keeps_it(client, database, make_case, declared_event):
    cid = make_case("Synthetic voucher reference choice")
    content = ("交易账号,交易时间,交易金额,交易余额,收付标志,交易对手账卡号,对手户名,摘要说明,"
               "交易流水号,凭证号,交易币种,交易是否成功\n"
               "SYN-A,2026-01-02 03:04:05,12.34,100.00,出,SYN-B,Synthetic counter,x,SYN-T,6222001,CNY,01\n").encode()
    if declared_event:
        header, row = content.decode().splitlines()
        content = (header + ",参考号机构,参考号渠道,参考号业务域,跨账簿事件参考号\n" +
                   row + ",SYN issuer,SYN rail,SYN scope,SYN event\n").encode()
    view = preview(client, cid, content)
    assert view["automatic"] is False
    assert view["stats"]["accepted"] == 0 and view["table_summary"]["unresolved"] == 1
    assert view["table_summary"]["reason_counts"] == {"conflicting_field": 1}
    assert view["table_summary"]["profile"] == ("funds-trace-table-v71" if declared_event else "funds-trace-table-v69")

    base = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    response = client.post(base + "/preview", json={"mode": view["mode"], "mapping": view["mapping"],
                                                    "reference_policy": "transaction-reference-v1"})
    assert response.status_code == 200, response.text
    resolved = response.json()
    assert resolved["stats"]["accepted"] == 1 and resolved["table_summary"]["unresolved"] == 0
    assert resolved["mapping"]["trade_no"] == "交易流水号"

    job = complete(client, cid, resolved)
    assert job["stage"] == "done" and job["result"]["ok"] == 1, job
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert [row["trade_no"] for row in flows] == ["SYN-T"] and flows[0]["amount"] == "12.34"
    package = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/v1/cases/{restored.json()['case_id']}/flows").json()["items"][0]["trade_no"] == "SYN-T"


@pytest.mark.parametrize("custom_mapping", [False, True])
def test_reference_choice_survives_ready_reopen_and_explicit_null_revokes_it(client, database, make_case, custom_mapping):
    cid = make_case("Synthetic reopened voucher choice")
    content = ("交易时间,付款方账号,收款方账号,交易金额,交易流水号,凭证号,自定义金额\n"
               "2026-01-02 03:04:05,SYN-A,SYN-B,12.34,SYN-T,SYN-V,12.34\n").encode()
    view = preview(client, cid, content)
    assert view["automatic"] is False and view["reference_policy_available"] is True
    base = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    body = {"mode": view["mode"], "mapping": view["mapping"], "reference_policy": "transaction-reference-v1"}
    if custom_mapping:
        body["mapping"] = {**view["mapping"], "amount": "自定义金额", "trade_no": "交易流水号"}
    resolved = client.post(base + "/preview", json=body).json()
    assert resolved["stats"]["accepted"] == 1
    assert resolved["reference_policy"] == "transaction-reference-v1"
    stored = client.get(f"/api/v1/cases/{cid}/imports", params={"import_id": view["import_id"]}).json()["items"][0]
    assert stored["request"]["reference_policy"] == "transaction-reference-v1"
    restored = client.post(base + "/preview", json={"mode": stored["mode"], "mapping": stored["mapping"]}).json()
    assert restored["stats"]["accepted"] == 1 and restored["reference_policy"] == "transaction-reference-v1"
    default = client.post(base + "/preview", json={"mode": restored["mode"], "mapping": restored["mapping"],
                                                   "reference_policy": None}).json()
    assert default["stats"]["accepted"] == 0 and default["reference_policy"] is None


def test_selected_reference_policy_survives_failure_and_database_reopen(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic durable reference choice")
    content = ("交易时间,付款方账号,收款方账号,交易金额,交易流水号,凭证号,自定义金额\n"
               "2026-01-02 03:04:05,SYN-A,SYN-B,12.34,SYN-T,SYN-V,12.34\n").encode()
    view = preview(client, cid, content)
    base = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    response = client.post(base + "/preview", json={"mode": view["mode"],
        "mapping": {**view["mapping"], "amount": "自定义金额", "trade_no": "交易流水号"},
        "reference_policy": "transaction-reference-v1"})
    assert response.status_code == 200, response.text
    selected = response.json()
    assert selected["stats"]["accepted"] == 1
    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic write failure")
    with monkeypatch.context() as failure:
        failure.setattr(importer, "commit_rows", fail)
        failed = complete(client, cid, selected)
    assert failed["stage"] == "error"
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid]) == [{"n": 0}]
    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    stored = client.get(f"/api/v1/cases/{cid}/imports", params={"import_id": view["import_id"]}).json()["items"][0]
    assert stored["request"]["reference_policy"] == "transaction-reference-v1"
    restored = client.post(base + "/preview", json={"mode": stored["mode"], "mapping": stored["mapping"]}).json()
    assert restored["stats"]["accepted"] == 1 and restored["reference_policy"] == "transaction-reference-v1"
    job = complete(client, cid, restored)
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]["trade_no"] == "SYN-T"


def test_old_ready39_and_new_source_keep_separate_versions_and_receipts(client, database, make_case, monkeypatch):
    from test_open_table_voucher_reference import payload, source_row
    cid = make_case("Synthetic pinned 39 source")
    content = payload([source_row()])
    with monkeypatch.context() as historical:
        historical.setattr(imports, "TABLE_PROFILE", table_import.SOURCE_OBSERVATION_PROFILE)
        old = preview(client, cid, content)
    old_base = f"/api/v1/cases/{cid}/imports/{old['import_id']}"
    old = client.post(old_base + "/preview", json={"mode": old["mode"], "mapping": old["mapping"]}).json()
    assert old["table_summary"]["profile"] == "funds-trace-table-v66" and old["stats"]["accepted"] == 0
    receipt = complete(client, cid, old)
    assert receipt["stage"] == "done" and receipt["result"]["table_summary"]["unresolved"] == 1
    current = preview(client, cid, content)
    assert current["table_summary"]["profile"] == "funds-trace-table-v69" and current["stats"]["accepted"] == 1
    job = complete(client, cid, current)
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    assert client.get(old_base).json()["result"] == receipt["result"]
    coverage = client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]
    assert coverage["unresolved_records"] == 0 and coverage["financial_interpretation_complete"] is True


def test_cataloged_references_keep_automatic_processing_without_a_redundant_choice(client, make_case):
    from test_open_table_voucher_reference import payload, source_row
    cid = make_case("Synthetic automatic 39 references")
    view = preview(client, cid, payload([source_row()]))
    assert view["table_summary"]["reason_counts"] == {"transaction_valid": 1}
    assert view["reference_policy_available"] is False and view["automatic"] is True


def test_commit_cannot_change_reference_choice_without_repreview(client, database, make_case):
    cid = make_case("Synthetic unpreviewed reference choice")
    content = ("交易时间,付款方账号,收款方账号,交易金额,交易流水号,凭证号\n"
               "2026-01-02 03:04:05,SYN-A,SYN-B,12.34,SYN-T,SYN-V\n").encode()
    view = preview(client, cid, content)
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/commit",
                           json={"mode": view["mode"], "mapping": view["mapping"],
                                 "reference_policy": "transaction-reference-v1"})
    assert response.status_code == 409, response.text
    assert database.q_raw("select stage from import_sessions where import_id=?", [view["import_id"]]) == [{"stage": "ready"}]


@pytest.mark.parametrize("surface", ["v1", "legacy"])
def test_archive_reference_choice_is_rejected_instead_of_ignored(client, make_case, surface):
    cid = make_case("Synthetic archive policy rejection")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("synthetic.csv", "交易时间,付款方账号,收款方账号,交易金额\n2026-01-01,SYN-A,SYN-B,1.00\n")
    if surface == "v1":
        view = preview(client, cid, buffer.getvalue(), "synthetic.zip")
        endpoint = f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview"
    else:
        view = client.post("/api/import/preview", params={"case_id": cid}, files={"file": ("synthetic.zip", buffer.getvalue())}).json()
        endpoint = "/api/import/repreview"
    body = {"mode": view["mode"], "mapping": view["mapping"], "reference_policy": "transaction-reference-v1"}
    if surface == "legacy":
        body.update(case_id=cid, import_id=view["import_id"])
    response = client.post(endpoint, json=body)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("historical", [False, True])
def test_reference_choice_rejects_legacy_and_historical_single_file_sessions(client, make_case, monkeypatch, historical):
    cid = make_case("Synthetic unsupported single file policy")
    content = ("交易时间,付款方账号,收款方账号,交易金额,交易流水号,凭证号\n"
               "2026-01-02 03:04:05,SYN-A,SYN-B,12.34,SYN-T,SYN-V\n").encode()
    if historical:
        with monkeypatch.context() as previous:
            previous.setattr(imports, "TABLE_PROFILE", table_import.SOURCE_OBSERVATION_PROFILE)
            view = preview(client, cid, content)
        endpoint = f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview"
    else:
        view = client.post("/api/import/preview", params={"case_id": cid}, files={"file": ("synthetic.csv", content)}).json()
        endpoint = "/api/import/repreview"
    body = {"mode": view["mode"], "mapping": view["mapping"], "reference_policy": "transaction-reference-v1"}
    if not historical:
        body.update(case_id=cid, import_id=view["import_id"])
    response = client.post(endpoint, json=body)
    assert response.status_code == 422, response.text


def test_reference_policy_is_a_no_op_without_a_voucher_column(client, database, make_case):
    cid = make_case("Synthetic policy without voucher")
    content = ("交易时间,付款方账号,收款方账号,交易金额,币种\n2026-01-01 10:00:00,SYN-A,SYN-B,1.00,CNY\n").encode()
    view = preview(client, cid, content)
    base = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    response = client.post(base + "/preview", json={"mode": view["mode"], "mapping": view["mapping"],
                                                    "reference_policy": "transaction-reference-v1"})
    assert response.status_code == 200, response.text
    same = response.json()
    assert same["stats"]["accepted"] == view["stats"]["accepted"] == 1
    assert same["mapping"] == view["mapping"]


@pytest.mark.parametrize("suffix", [".csv", ".xlsx", ".xls"])
def test_complete39_reference_roles_survive_package_round_trip(client, database, make_case, suffix):
    cid = make_case("Synthetic 39 column package")
    headers = ("交易卡号,交易账号,主户名,开户人证件号码,交易日期,交易时间,交易净额,交易金额,交易余额,收付标志,"
               "交易对手账卡号,现金标志,对手户名,是否调取,已调取账号对手身份,对手身份证号,对手开户银行,摘要说明,"
               "交易币种,交易网点名称,交易发生地,交易是否成功,传票号,IP地址,MAC地址,对手交易余额,交易流水号,"
               "日志号,凭证种类,凭证号,交易柜员号,备注,商户名称,交易类型,查询反馈结果原因,备用字段1,备用字段2,"
               "备用字段3,重点字样")
    rows = [",".join(["SYN-CARD", "SYN-A", "Synthetic own", "SYN-ID", "2026-01-02", "2026-01-02 03:04:05",
                      "-12.34", "12.34", "100.00", "出", "SYN-B", "00", "Synthetic counter", "未调取", "",
                      "", "Synthetic bank", "transfer", "CNY", "Synthetic branch", "", "01", "", "", "", "",
                      "SYN-T", "", "4001", "SYN-CARD", "", "", "Synthetic merchant", "转账", "成功", "", "", "", ""])]
    content = (headers + "\n" + "\n".join(rows) + "\n").encode()
    if suffix != ".csv":
        import csv
        cells = list(csv.reader(io.StringIO(content.decode())))
        if suffix == ".xls":
            from xls_fixtures import workbook
            content = workbook([("Synthetic39", cells)])
        else:
            from test_table_multisheet_append import encoded
            content = encoded([("Synthetic39", cells)], suffix)
    view = preview(client, cid, content, "synthetic" + suffix)
    assert view["table_summary"]["profile"] == ("funds-trace-table-v70" if suffix == ".xls" else "funds-trace-table-v69")
    assert view["stats"]["accepted"] == 1 and view["table_summary"]["unresolved"] == 0
    job = complete(client, cid, view)
    assert job["result"]["ok"] == 1, job

    package = client.get(f"/api/cases/{cid}/export")
    assert package.status_code == 200, package.text
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    flows = client.get(f"/api/v1/cases/{restored.json()['case_id']}/flows").json()["items"]
    assert [(row["trade_no"], row["amount"]) for row in flows] == [("SYN-T", "12.34")]


def test_trial_complete39_keeps_source_holder_in_both_directions_and_replays_pinned_versions(
        client, database, make_case, monkeypatch):
    from test_open_table_voucher_reference import COLUMNS, source_row
    from test_ready_import_evidence import check, position
    from test_table_multisheet_append import encoded

    values = [source_row(**{"交易流水号": "SYN-OUT"}), source_row(**{
        "交易流水号": "SYN-IN", "交易时间": "2026-09-01 10:01:00", "交易净额": "25.30",
        "交易金额": "25.30", "交易余额": "925.30", "收付标志": "进", "对手交易余额": "111.11",
    })]
    raw = encoded([("Synthetic39", [COLUMNS, *[tuple(row.get(key, "") for key in COLUMNS) for row in values]])], ".xlsx")
    old_case, case = make_case("Synthetic original parser"), make_case("Synthetic trial parser")
    old = preview(client, old_case, raw, "synthetic.xlsx")
    assert old["table_summary"]["profile"] == "funds-trace-table-v69"
    assert all(row["self_name"] == "" for row in old["preview"])

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    new = preview(client, case, raw, "synthetic.xlsx")
    expected = {"SYN-OUT": ("Synthetic own", "Synthetic own", "Synthetic counter", "100.00", "900.00"),
                "SYN-IN": ("Synthetic own", "Synthetic counter", "Synthetic own", "25.30", "925.30")}
    keys = ("self_name", "payer_name", "payee_name", "amount", "balance")
    assert {row["trade_no"]: tuple(row[key] for key in keys) for row in new["preview"]} == expected
    assert new["table_summary"]["profile"] == "funds-trace-table-v95"
    assert new["mapping"]["self_name"] == "主户名"
    [scope] = new["ready_evidence"]["scopes"]
    assert scope["sample_rows"] == [2, 3]
    verdict = check(client, case, new, "self_name", [position(scope, 3)])
    assert verdict.status_code == 200, verdict.text
    assert verdict.json()["evidence_status"] == "supported"

    for target, view in ((old_case, old), (case, new)):
        address = f"/api/v1/cases/{target}/imports/{view['import_id']}/preview"
        prepared = client.post(address, json={"mode": view["mode"], "mapping": view["mapping"]})
        assert prepared.status_code == 200, prepared.text
        assert prepared.json()["table_summary"]["profile"] == view["table_summary"]["profile"]
        assert prepared.json()["mapping"].get("self_name") == view["mapping"].get("self_name")
        receipt = complete(client, target, prepared.json())
        assert receipt["stage"] == "done" and receipt["result"]["ok"] == 2, receipt

    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    reread = client.get(f"/api/v1/cases/{case}/flows").json()["items"]
    assert {row["trade_no"]: tuple(row[key] for key in keys) for row in reread} == expected
    reread_old = client.get(f"/api/v1/cases/{old_case}/flows").json()["items"]
    assert len(reread_old) == 2 and all(row["self_name"] == "" for row in reread_old)
    coverage = client.get(f"/api/v1/cases/{case}/stats").json()["import_coverage"]
    assert coverage["unresolved_records"] == 0 and coverage["financial_interpretation_complete"] is True

    package = client.get(f"/api/cases/{case}/export")
    assert package.status_code == 200, package.text
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    flows = client.get(f"/api/v1/cases/{restored.json()['case_id']}/flows").json()["items"]
    assert {row["trade_no"]: tuple(row[key] for key in keys) for row in flows} == expected


def test_trial_complete39_does_not_share_holder_rule_with_neighboring_near_match(client, make_case, monkeypatch):
    from test_open_table_voucher_reference import COLUMNS, source_row
    from test_table_multisheet_append import encoded

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    near = (*COLUMNS[:-1], "姓名(查询条件)")
    full_row = source_row()
    near_row = source_row(**{"主户名": "Synthetic unsupported", "姓名(查询条件)": "Synthetic query name",
                             "交易流水号": "SYN-NEAR", "凭证号": "SYN-NEAR"})
    raw = encoded([("Near", [near, tuple(near_row.get(key, "") for key in near)]),
                   ("Complete", [COLUMNS, tuple(full_row.get(key, "") for key in COLUMNS)])], ".xlsx")
    case = make_case("Synthetic neighboring table")
    new = preview(client, case, raw, "synthetic.xlsx")
    assert new["table_summary"]["profile"] == "funds-trace-table-v95"
    assert {row["trade_no"]: row["self_name"] for row in new["preview"]} == {
        "SYN-TRANSACTION": "Synthetic own", "SYN-NEAR": ""}
    assert [(item["member_path"], item["column_index"], item["column"])
            for item in new["table_summary"]["mapping_evidence"] if item["field"] == "self_name"] == [
                ("worksheets/0002", "3", "主户名")]


def test_trial_complete39_unreadable_holder_keeps_monetary_rows_after_unrelated_mapping(
        client, database, make_case, monkeypatch):
    from test_open_table_voucher_reference import COLUMNS, source_row
    from test_table_multisheet_append import encoded

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    source = [source_row(**{"主户名": holder, "交易流水号": f"SYN-{index}", "备注": "Synthetic manual channel"})
              for index, holder in enumerate(('="Synthetic formula"', "#N/A", 12345))]
    rows = [COLUMNS, *[tuple(row.get(key, "") for key in COLUMNS) for row in source]]
    case = make_case("Synthetic holder cell types")
    view = preview(client, case, encoded([("Synthetic39", rows)], ".xlsx"), "synthetic.xlsx")
    assert view["stats"]["accepted"] == 3
    assert all(row["self_name"] == row["payer_name"] == "" for row in view["preview"])
    address = f"/api/v1/cases/{case}/imports/{view['import_id']}/preview"
    mapping = {**view["mapping"], "channel": "备注"}
    assert mapping != view["mapping"]
    released = client.post(address, json={"mode": view["mode"], "mapping": mapping})
    assert released.status_code == 200, released.text
    prepared = released.json()
    assert prepared["stats"]["accepted"] == 3
    basis = {(item["field"], item["column"]): item["basis"]
             for item in prepared["table_summary"]["mapping_evidence"]}
    assert basis["self_name", "主户名"] == "explicit_identity_role"
    assert basis["channel", "备注"] == "manual_override"
    assert all(row["self_name"] == row["payer_name"] == "" for row in prepared["preview"])
    receipt = complete(client, case, prepared)
    assert receipt["stage"] == "done" and receipt["result"]["ok"] == 3, receipt
    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    reread = client.get(f"/api/v1/cases/{case}/flows").json()["items"]
    assert len(reread) == 3 and all(row["self_name"] == row["payer_name"] == "" for row in reread)


SPLIT_LEDGER_HEADERS = ("协查名称", "记录注记", "账户号", "业务注记", "记录类型", "币种", "交易日期", "交易时间",
                        "渠道注记", "其他注记", "收入", "支出", "余额", "对方账号")


@pytest.mark.parametrize("manual_clock", [False, True])
def test_trial_split_ledger_reads_literal_compact_clock_and_keeps_relative_roles(
        client, make_case, monkeypatch, manual_clock):
    from test_ready_import_evidence import check, position
    from test_table_multisheet_append import encoded

    def posting(day, time, received="10.23", paid="", balance="20.50"):
        return ("Synthetic search subject", "", "000SYN-A", "", "", "CNY", day, time,
                "", "", received, paid, balance, "000SYN-B")

    rows = [SPLIT_LEDGER_HEADERS, posting("20260228", "20260228 09:10:11"),
            posting("20260228", "20260228 09:15:12", "", "4.25", "16.25"),
            posting("20260228", "20260228 09:20:13", "0.00", "0.00", "16.25"),
            posting("20260228", "20260228 24:01:00"), posting("20260227", "20260228 10:00:00"),
            posting("20260230", "20260228 10:05:00"), posting(20260228, "20260228 10:10:00"),
            posting("", "20260228 10:15:00"), posting("20260228", "20260228 10:20:00+08:00")]
    sheets = [("SyntheticLedger", rows)]
    if not manual_clock:
        sheets.append(("SyntheticAux", [("协查名称", "辅助注记"), ("Synthetic auxiliary subject", "Synthetic note")]))
    raw = encoded(sheets, ".xlsx")
    case = make_case("Synthetic compact clock ledger")
    legacy = preview(client, case, raw, "synthetic.xlsx")
    assert (legacy["table_summary"]["profile"], legacy["stats"]["accepted"]) == ("funds-trace-table-v69", 0)
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    view = preview(client, case, raw, "synthetic.xlsx")
    summary = view["table_summary"]
    assert (summary["accepted"], summary["non_monetary"], summary["unresolved"]) == (2, 1, 6 if manual_clock else 8)
    assert summary["profile"] == "funds-trace-table-v96"
    assert view["mapping"]["self_account"] == "账户号" and "self_name" not in view["mapping"]
    [scope] = view["ready_evidence"]["scopes"]
    verdict = check(client, case, view, "self_account", [position(scope, 3)])
    assert verdict.status_code == 200, verdict.text
    assert verdict.json()["reason"] == ("registered_source_role" if manual_clock else "incomplete_source_scope")

    base = f"/api/v1/cases/{case}/imports/{view['import_id']}"
    mapping = dict(view["mapping"])
    if manual_clock:
        mapping.update(trade_time="交易时间", channel="渠道注记")
    prepared = client.post(base + "/preview", json={"mode": view["mode"], "mapping": mapping})
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["table_summary"]["profile"] == "funds-trace-table-v96"
    receipt = complete(client, case, prepared.json())
    assert receipt["stage"] == "done" and receipt["result"]["ok"] == 2, receipt
    flows = client.get(f"/api/v1/cases/{case}/flows").json()["items"]
    assert {(row["trade_time"], row["payer_account"], row["payee_account"], row["amount"], row["balance"])
            for row in flows if row["amount"] != "0.00"} == {
        ("2026-02-28 09:10:11", "000SYN-B", "000SYN-A", "10.23", "20.50"),
        ("2026-02-28 09:15:12", "000SYN-A", "000SYN-B", "4.25", "16.25"),
    }
    assert all(row["self_name"] == "" and row["trade_time_utc"] is None for row in flows)
    src = f"/api/v1/cases/{case}/sources/{view['import_id']}"
    report = client.get(src + "/report").json()["items"]
    assert [(row["source_row"], row["reason_code"]) for row in report if row["member_path"] == "worksheets/0001"] == [
        (2, "transaction_valid"), (3, "transaction_valid"), (4, "zero_amount"), (5, "invalid_time"),
        (6, "conflicting_time"), (7, "invalid_time"), (8, "invalid_time"), (9, "missing_time"), (10, "invalid_time"),
    ]
    if not manual_clock:
        assert any(row["reason_code"] == "unknown_table" and row["member_path"] == "worksheets/0002" for row in report)
    assert client.get(src + "/file").content == raw
    stored = client.get(base + "/records", params={"member_path": "worksheets/0001", "source_row": 2}).json()["items"]
    [record] = stored
    clock = json.loads(record["details"]["source_time_evidence"])["values"]
    assert [(value["column_index"], value["raw"]) for value in clock] == [(7, "20260228"), (8, "20260228 09:10:11")]

    package = client.get(f"/api/cases/{case}/export")
    assert package.status_code == 200, package.text
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    reread = client.get(f"/api/v1/cases/{restored.json()['case_id']}/flows").json()["items"]
    assert {row["trade_time"] for row in reread} == {row["trade_time"] for row in flows}


@pytest.mark.parametrize("extra", ["missing", "duplicate", "competing_owner"])
def test_trial_split_ledger_requires_unambiguous_physical_core(client, make_case, monkeypatch, extra):
    from test_table_multisheet_append import encoded

    headers = list(SPLIT_LEDGER_HEADERS)
    if extra == "missing":
        headers[-1] = "对方证件号码"
    else:
        headers.append("账户号" if extra == "duplicate" else "本方账号")
    values = {"账户号": "000SYN-A", "对方账号": "000SYN-B", "收入": "10.00", "余额": "20.00",
              "币种": "CNY", "交易日期": "20260228", "交易时间": "20260228 09:10:11"}
    rows = [headers, [values.get(key, "") for key in headers]]
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic ambiguous compact ledger")
    view = preview(client, case, encoded([("SyntheticNear", rows)], ".xlsx"), "synthetic.xlsx")
    assert (view["table_summary"]["profile"], view["stats"]["accepted"]) == ("funds-trace-table-v69", 0)


ACCOUNT_INQUIRY_HEADERS = ("调查户名", "开户银行网点", "证件号", "客户号", "本期账户号", "前期账户号", "账户类型",
                           "开户日期", "销户日期", "项目类别", "记录标识", "查询说明", "备注")


def account_inquiry_rows(count):
    return [ACCOUNT_INQUIRY_HEADERS, *[("Synthetic query", "Synthetic branch", f"SYN-ID-{index}", f"SYN-C-{index}",
        f"SYN-NEW-{index}", f"SYN-OLD-{index}", "Synthetic type", "2020-02-03", "", "", "", "", "")
        for index in range(count)]]


@pytest.mark.parametrize("blank_tail", [False, True])
def test_trial_account_inquiry_registers_original_rows_without_authorizing_identity(client, database, make_case,
                                                                                   monkeypatch, blank_tail):
    from test_table_multisheet_append import encoded
    from test_three_workflow_intake import material

    transaction = [("账户号", "对方账号", "收入", "支出", "余额", "交易日期", "交易时间", "币种"),
                   ("SYN-BOOK", "SYN-PEER", "1.03", "", "2.05", "20260703", "20260703 16:18:19", "CNY")]
    inquiry_rows = account_inquiry_rows(14)
    if blank_tail:
        inquiry_rows[0] = (*ACCOUNT_INQUIRY_HEADERS[:10], "备注", "", "")
    raw = encoded([("Synthetic inquiry", inquiry_rows), ("Synthetic ledger", transaction)], ".xlsx")
    case = make_case("Synthetic registered inquiry")
    view = preview(client, case, raw, "synthetic.xlsx")
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"
    assert view["table_summary"]["auxiliary"] == 0
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    view = preview(client, case, raw, "synthetic.xlsx")
    source = view["import_id"]
    receipt = material(client, case, source)
    assert any(item["role"] == "account" for item in receipt["items"])
    assert view["table_summary"]["profile"] == "funds-trace-table-v97"
    address = f"/api/v1/cases/{case}/imports/{source}/preview"
    prepared = client.post(address, json={"mode": view["mode"], "mapping": view["mapping"]})
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["table_summary"]["profile"] == "funds-trace-table-v97"
    done = complete(client, case, prepared.json())
    assert done["stage"] == "done" and done["result"]["ok"] == 1, done
    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    items = {item["member_path"]: item for item in material(client, case, source)["items"]}
    inquiry, ledger = items["worksheets/0001"], items["worksheets/0002"]
    assert (inquiry["role"], inquiry["status"], inquiry["header_row"], inquiry["last_row"]) == ("account", "registered", 1, 15)
    assert (inquiry["auxiliary"], inquiry["accepted"], inquiry["unresolved"]) == (14, 0, 0)
    assert (ledger["role"], ledger["accepted"], ledger["unresolved"]) == ("transaction", 1, 0)
    response = client.get(inquiry["records_url"])
    assert response.status_code == 200, response.text
    records = response.json()["items"]
    assert [row["source_row"] for row in records] == list(range(2, 16))
    assert all(row["flow_id"] is None and row["details"]["source_role"] == "account"
               and "resolution_evidence" not in row["details"] for row in records)
    assert client.get(inquiry["original_url"]).content == raw
    response = client.post(f"/api/v1/cases/{case}/trace-ledgers", json={
        "kind": "name", "value": "Synthetic query", "start": "2020-02-03", "end": "2026-07-03"})
    assert response.status_code == 200 and response.json()["choices"] == []
    historic = table_import.parse(raw, suffix=".xlsx", profile="funds-trace-table-v96")
    assert not any(member["role"] == "auxiliary" for member in historic["members"])
    assert len(historic["rows"]) == 1
    package = client.get(f"/api/cases/{case}/export")
    assert package.status_code == 200, package.text
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    restored_case = restored.json()["case_id"]
    [saved] = database.q_raw("select source_id from import_sources where case_id=?", [restored_case])
    restored_items = material(client, restored_case, saved["source_id"])["items"]
    [restored_inquiry] = [item for item in restored_items if item["role"] == "account"]
    assert (restored_inquiry["status"], restored_inquiry["auxiliary"], restored_inquiry["accepted"]) == ("registered", 14, 0)


@pytest.mark.parametrize("mixed", [False, True])
def test_trial_account_inquiry_does_not_hide_a_transaction_region(client, make_case, monkeypatch, mixed):
    from test_table_multisheet_append import encoded
    from test_three_workflow_intake import material

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    observed = [("交易时间", "本方账号", "对方账号", "收入"),
                ("2026-07-03 16:18:19", "SYN-BOOK", "SYN-PEER", "1.03")]
    rows = account_inquiry_rows(1)
    if mixed:
        rows.extend(observed)
    else:
        rows[0] = (*rows[0][:9], "交易时间", "收入", *rows[0][11:])
    case = make_case("Synthetic mixed inquiry region")
    view = preview(client, case, encoded([("Synthetic near inquiry", rows)], ".xlsx"), "synthetic.xlsx")
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"
    assert not any(item["role"] == "account" for item in material(client, case, view["import_id"])["items"])
    if mixed:
        assert view["table_summary"]["accepted"] == 1


@pytest.mark.parametrize("feedback39", [False, True])
def test_trial_account_inquiry_requires_the_split_ledger_workbook(client, make_case, monkeypatch, feedback39):
    from test_open_table_voucher_reference import COLUMNS, source_row
    from test_table_multisheet_append import encoded
    from test_three_workflow_intake import material

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    sheets = [("Synthetic inquiry", account_inquiry_rows(1))]
    if feedback39:
        row = source_row()
        sheets.append(("Synthetic feedback", [COLUMNS, [row.get(heading, "") for heading in COLUMNS]]))
    case = make_case("Synthetic workbook scope")
    view = preview(client, case, encoded(sheets, ".xlsx"), "synthetic.xlsx")
    expected_profile = 95 if feedback39 else 69
    assert view["table_summary"]["profile"] == f"funds-trace-table-v{expected_profile}"
    assert not any(item["role"] == "account" for item in material(client, case, view["import_id"])["items"])
    if feedback39:
        [flow] = view["preview"]
        assert flow["self_name"] == flow["payer_name"] == "Synthetic own"


def coded_bank_csv(operations, *, changed_heading=None):
    labels = {3: "交易日期", 4: "交易时间", 6: "客户名称", 7: "交易卡号", 8: "交易流水", 11: "账户代码",
              12: "交易方向(D:借|C:贷)", 13: "币种名称", 14: "交易金额", 15: "联机余额", 16: "摘要名称",
              17: "文字摘要", 21: "对方帐号", 22: "对方客户名称", 23: "对方帐号开户机构名称",
              49: "交易摘要", 52: "币种", 54: "证件号"}
    if changed_heading:
        column, label = changed_heading
        labels[column] = label
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([labels.get(index, f"未映射列{index}") for index in range(1, 56)])
    for ordinal, (marker, amount, balance, currency, code, summary, counterparty) in enumerate(operations):
        values = {3: "2026-07-03", 4: f"\t16:18:{ordinal:02d}\t", 6: "合成户名", 7: "000SYN-CARD",
                  8: f"SYN-ROW-{ordinal}", 11: "000SYN-ACCOUNT", 12: f"\t{marker}\t", 13: currency,
                  14: amount, 15: balance, 16: summary, 21: counterparty, 22: "合成对方" if counterparty else "",
                  23: "合成对方机构" if counterparty else "", 52: code, 54: "SYN-DOC"}
        writer.writerow([values.get(index, "") for index in range(1, 56)])
    return output.getvalue().encode("gbk")


def test_trial_coded_bank_csv_keeps_signs_exact_and_leaves_reversal_unpaired(client, database, make_case, monkeypatch):
    case = make_case("Synthetic coded bank CSV")
    payload = coded_bank_csv([
        ("C:贷", "25.13", "100.13", "人民币", "10", "", "SYN-PEER"),
        ("D:借", "-4.25", "95.88", "人民币", "10", "", "SYN-PEER"),
        ("C:贷", "-1.25", "94.63", "人民币", "10", "转账冲正", ""),
        ("C:借", "7.00", "101.63", "人民币", "10", "", "SYN-PEER"),
        ("D:借", "-2.00", "99.63", "美元", "10", "", "SYN-PEER"),
    ])
    old = preview(client, case, payload)
    assert (old["table_summary"]["profile"], old["table_summary"]["accepted"]) == ("funds-trace-table-v69", 0)
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    view = preview(client, case, payload)
    assert (view["table_summary"]["accepted"], view["table_summary"]["unresolved"]) == (2, 3)
    assert view["table_summary"]["profile"] == "funds-trace-table-v98"
    assert view["automatic"] is True and view["layout_model_trial"] is False
    assert (view["mapping"]["self_account"], view["mapping"]["self_card"], view["mapping"]["direction"]) == (
        "账户代码", "交易卡号", "交易方向(D:借|C:贷)")
    base = f"/api/v1/cases/{case}/imports/{view['import_id']}"
    receipt = complete(client, case, view)
    assert receipt["stage"] == "done" and receipt["result"]["ok"] == 2, receipt
    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    flows = client.get(f"/api/v1/cases/{case}/flows?granularity=observations").json()["items"]
    keys = ("trade_no", "payer_account", "payee_account", "amount", "balance", "currency", "trade_time")
    assert {tuple(row[key] for key in keys) for row in flows} == {
        ("SYN-ROW-0", "SYN-PEER", "000SYN-ACCOUNT", "25.13", "100.13", "CNY", "2026-07-03 16:18:00"),
        ("SYN-ROW-1", "000SYN-ACCOUNT", "SYN-PEER", "4.25", "95.88", "CNY", "2026-07-03 16:18:01"),
    }
    records = client.get(base + "/records").json()["items"]
    assert [json.loads(row["details"]["source_ledger_evidence"])["own_card"] for row in records[:2]] == [
        "000SYN-CARD", "000SYN-CARD"]
    assert [(row["source_row"], row["reason_code"]) for row in records] == [
        (2, "transaction_valid"), (3, "transaction_valid"), (4, "conflicting_direction"),
        (5, "invalid_direction"), (6, "invalid_currency"),
    ]
    exception = records[2]
    assert exception["flow_id"] is None and exception["disposition"] == "unresolved"
    witness = {cell["column_index"]: cell["raw"] for cell in json.loads(exception["details"]["source_field_evidence"])}
    assert {column: witness[column].strip() for column in (12, 14, 15, 16)} == {
        12: "C:贷", 14: "-1.25", 15: "94.63", 16: "转账冲正"}
    assert client.get(f"/api/v1/cases/{case}/sources/{view['import_id']}/file").content == payload


def test_trial_coded_bank_csv_does_not_grant_roles_to_a_similar_header(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic near bank CSV")
    payload = coded_bank_csv([("D:借", "-4.25", "95.88", "人民币", "10", "", "SYN-PEER")],
                             changed_heading=(15, "客户查询余额"))
    view = preview(client, case, payload)
    assert (view["table_summary"]["profile"], view["table_summary"]["accepted"]) == ("funds-trace-table-v69", 0)
    assert view["layout_model_trial"] is True


def test_trial_coded_bank_csv_keeps_layout_review_for_a_competing_owner(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic competing bank CSV owner")
    payload = coded_bank_csv([
        ("C:贷", "25.13", "100.13", "人民币", "10", "", "SYN-PEER"),
        ("D:借", "-4.25", "95.88", "人民币", "10", "", "SYN-PEER"),
    ], changed_heading=(2, "本方账号"))
    rows = list(csv.reader(io.StringIO(payload.decode("gbk"), newline="")))
    rows[2][1] = "SYN-DIFFERENT-ACCOUNT"
    output = io.StringIO(newline="")
    csv.writer(output).writerows(rows)
    view = preview(client, case, output.getvalue().encode("gbk"))
    assert view["table_summary"]["profile"] == "funds-trace-table-v98"
    assert view["table_summary"]["reason_counts"] == {"transaction_valid": 1, "conflicting_field": 1}
    assert view["layout_model_trial"] is True


def _precision_clock_source(*, currency=True, minute_heading="交易时间", cases=None, account_first=False):
    headers = ["交易日期", "交易时间", minute_heading, "交易账号", "对方账号", "借方发生额", "贷方发生额", "交易余额"]
    if currency:
        headers.append("币种")
    if cases is None:
        cases = [("08:12:34", "2044/5/1 8:12")]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    positions = [3, 0, 1, 2, *range(4, len(headers))] if account_first else range(len(headers))
    writer.writerow([headers[index] for index in positions])
    for precise, comparison in cases:
        cells = ["2044-05-01", precise, comparison, "000701", "000702", "5.36", "0.00", "109.79",
                 *(["CNY"] if currency else [])]
        writer.writerow([cells[index] for index in positions])
    return stream.getvalue().encode()


@pytest.mark.parametrize("account_first", [False, True], ids=["leading-time-columns", "account-before-clocks"])
def test_trial_corroborates_minute_time_and_retains_explicit_seconds_through_cold_and_package(
        client, database, make_case, monkeypatch, tmp_path, account_first):
    from test_cases import archive_of, exported, import_package, members, rewrite_parquet

    raw = _precision_clock_source(account_first=account_first, cases=[
        ("08:12:34", "2044/5/1 8:12"),
        ("08:12:34", "2044/5/2 8:12"),
        ("08:12:34", "2044/5/1 8:13"),
        ("08:12:34", "2044/5/1 8:12:00"),
        ("08:12:34", "2044/5/1 8:12:35"),
        ("08:12", "2044/5/1 8:12"),
        ("", "2044/5/1 8:12"),
        ("08:12:34", "2044-05-01 08:12:34+08:00"),
    ])
    cid = make_case("Synthetic wall clock corroboration")
    old_cid = make_case("Synthetic frozen preview")
    old = preview(client, old_cid, raw)
    assert old["table_summary"]["profile"] == "funds-trace-table-v69"
    assert old["table_summary"]["reason_counts"]["conflicting_time"] >= 5
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    check_old = client.post(f"/api/v1/cases/{old_cid}/imports/{old['import_id']}/preview", json={
        "mode": old["mode"], "mapping": old["mapping"],
    }).raise_for_status().json()
    assert check_old["table_summary"]["profile"] == "funds-trace-table-v69"
    assert check_old["table_summary"]["reason_counts"] == old["table_summary"]["reason_counts"]

    view = preview(client, cid, raw)
    assert view["table_summary"]["profile"] == "funds-trace-table-v102"
    assert view["table_summary"]["accepted"] == 1
    result = complete(client, cid, view)
    assert result["stage"] == "done" and result["result"]["ok"] == 1
    source = view["import_id"]
    base = f"/api/v1/cases/{cid}"
    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    rows = client.get(base + f"/imports/{source}/records", params={"member_path": "table.csv"}).raise_for_status().json()["items"]
    by_position = {row["source_row"]: row for row in rows}
    assert [by_position[n]["reason_code"] for n in (3, 4, 5, 6)] == ["conflicting_time"] * 4
    assert all(by_position[n]["disposition"] == "unresolved" and by_position[n]["flow_id"] is None for n in (7, 8, 9))
    assert by_position[2]["details"]["source_trade_time_corroborated"] == "2044-05-01 08:12:34"
    assert all("source_trade_time_corroborated" not in by_position[pos]["details"] for pos in range(3, 10))
    evidence = json.loads(by_position[2]["details"]["source_time_evidence"])
    first_date = 2 if account_first else 1
    assert [(item["column_index"], item["raw"]) for item in evidence["values"]] == [
        (first_date, "2044-05-01"), (first_date + 1, "08:12:34"), (first_date + 2, "2044/5/1 8:12")]
    assert all(item["role"] == "transaction" and item["basis"] == "source_wall_clock" for item in evidence["values"])
    flow = client.get(base + "/flows", params={"granularity": "observations"}).raise_for_status().json()["items"]
    assert [(item["trade_time"], item["currency"], item["amount"]) for item in flow] == [
        ("2044-05-01 08:12:34", "CNY", "5.36")]
    assert client.get(base + f"/sources/{source}/file").content == raw
    transfer = exported(client, cid)
    package = import_package(client, transfer).raise_for_status().json()
    restored = client.get(f"/api/v1/cases/{package['case_id']}/flows", params={"granularity": "observations"}).raise_for_status().json()
    assert [(f["trade_time"], f["currency"], f["amount"]) for f in restored["items"]] == [
        ("2044-05-01 08:12:34", "CNY", "5.36")]
    entries = members(transfer)
    rewrite_parquet(entries, "import_records", """update rows set
        details_json=cast(json_merge_patch(details_json, '{"source_trade_time_corroborated":"2044-05-01 08:12:00"}') as varchar)
        where source_row=2""", tmp_path)
    changed = import_package(client, archive_of(entries))
    assert changed.status_code == 400 and "来源细时钟与保留时间依据" in changed.json()["detail"]


def test_trial_corroborated_clock_does_not_fill_currency_or_upgrade_archive(client, make_case, monkeypatch):
    from test_folder_evidence import preview as folder_preview

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Synthetic clock without currency")
    absent = _precision_clock_source(currency=False)
    view = preview(client, cid, absent)
    assert view["table_summary"]["profile"] == "funds-trace-table-v102"
    assert view["table_summary"]["reason_counts"] == {"invalid_currency": 1}
    saved = complete(client, cid, view)
    assert saved["stage"] == "done" and saved["result"]["ok"] == 0
    source = view["import_id"]
    base = f"/api/v1/cases/{cid}"
    [retained] = client.get(base + f"/imports/{source}/records", params={"member_path": "table.csv"}).raise_for_status().json()["items"]
    assert retained["flow_id"] is None and retained["reason_code"] == "invalid_currency"
    assert "source_ledger_evidence" not in retained["details"]
    assert retained["details"]["source_trade_time_corroborated"] == "2044-05-01 08:12:34"
    proof = json.loads(retained["details"]["source_time_evidence"])
    assert [(item["column_index"], item["raw"]) for item in proof["values"]] == [
        (1, "2044-05-01"), (2, "08:12:34"), (3, "2044/5/1 8:12")]
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0

    archive_cid = make_case("Synthetic archive with same input")
    capable = _precision_clock_source()
    archive = folder_preview(client, archive_cid, [("test-clock.csv", capable)], zipped=True, scope_bank=None)
    assert archive["table_summary"]["profile"] != "funds-trace-table-v102"
    assert archive["table_summary"]["reason_counts"] == {"conflicting_time": 1}

    other = make_case("Synthetic distinct clock roles")
    posting = preview(client, other, _precision_clock_source(minute_heading="记账时间"))
    assert posting["table_summary"]["profile"] == "funds-trace-table-v69"


def test_archive_trial_profile_does_not_run_or_restore_standalone_clock_precision(
        client, make_case, monkeypatch, tmp_path):
    from test_cases import archive_of, exported, import_package, members, rewrite_parquet
    from test_three_workflow_intake import trial_directory

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Synthetic directory without direct clock precision")
    native = coded_bank_csv([("D:借", "-4.25", "95.88", "人民币", "10", "", "SYN-PEER")])
    view = trial_directory(client, cid, [("coded.csv", native), ("time.csv", _precision_clock_source())])
    assert view["table_summary"]["profile"] == "funds-trace-table-archive-v69"
    assert view["table_summary"]["reason_counts"] == {"transaction_valid": 1, "conflicting_time": 1}
    result = complete(client, cid, view)
    assert result["stage"] == "done" and result["result"]["ok"] == 1
    stored = exported(client, cid)
    assert import_package(client, stored).status_code == 200

    entries = members(stored)
    rewrite_parquet(entries, "import_records", """update rows set
        details_json=cast(json_merge_patch(details_json, '{"trial_member_parser_version":"funds-trace-table-v102"}') as varchar)
        where member_path='coded.csv'""", tmp_path)
    denied = import_package(client, archive_of(entries))
    assert denied.status_code == 400 and "目录叶成员来源规则" in denied.json()["detail"]


def test_trial_corroborated_clock_stays_on_old_profile_if_another_region_is_ambiguous(
        client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic ambiguous second clock region")
    raw = _precision_clock_source() + (
        "2044-05-02,交易时间,,交易账号,对方账号,10.00,交易金额,收付标志,币种\r\n"
        ",2044-05-02 08:19:00,,000710,000720,,6.00,出,CNY\r\n"
    ).encode()
    result = preview(client, case, raw)
    summary = result["table_summary"]
    assert summary["documents"][0]["header_rows"] == [1]
    assert summary["profile"] == "funds-trace-table-v69" and summary["accepted"] == 0
    assert summary["reason_counts"] == {"conflicting_time": 1, "ambiguous_region_header": 2}
