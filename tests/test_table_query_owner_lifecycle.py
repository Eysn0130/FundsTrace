"""Synthetic public lifecycle for the bounded uniform query-owner contract."""
import io
import json
import zipfile
from decimal import Decimal

from fastapi import HTTPException
import pytest

from app import case_packages, table_import
from app.services import imports
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_feedback_package import _archive, _members, _mutate
from test_table_contract_lifecycle import facts, source_records
from test_table_mapping_coverage import coverage
from test_table_mapping_versions import profile_preview
from test_table_source_role_lifecycle import csv_bytes, transaction, xlsx_bytes
from test_table_source_roles import ACCOUNT, TRANSACTION_EXPLICIT, TRANSACTION_QUERY_ONLY


NEW_PROFILE = "funds-trace-table-v14"


@pytest.fixture(autouse=True)
def retained_v14_default(monkeypatch):
    # These tests certify the frozen v14 contract independently of new defaults.
    monkeypatch.setattr(imports, "TABLE_PROFILE", NEW_PROFILE)


def query_row(**changes):
    return transaction(**{"查询帐号": "SYN-OWN-ACCOUNT", "查询卡号": "SYN-OWN-CARD",
                          "交易对方账卡号": "SYN-OTHER", **changes})


def query_headers(enterprise=False):
    if not enterprise:
        return TRANSACTION_QUERY_ONLY
    return ["企业名称(查询条件)" if header == "姓名(查询条件)" else
            "合成证照(查询条件)" if header == "证件号码(查询条件)" else header
            for header in TRANSACTION_QUERY_ONLY]


def payload(headers, rows, suffix):
    return (csv_bytes(headers, rows) if suffix == ".csv" else
            xlsx_bytes([("Synthetic uniform transactions", headers, rows)]))


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("enterprise", [False, True])
@pytest.mark.parametrize("profile", [NEW_PROFILE, "funds-trace-table-v15", "funds-trace-table-v16", "funds-trace-table-v17"])
def test_uniform_query_owner_automatic_import_exact_facts_sources_and_package(
        client, database, make_case, monkeypatch, suffix, enterprise, profile):
    monkeypatch.setattr(imports, "TABLE_PROFILE", profile)
    cid = make_case("Synthetic uniform query owner")
    headers = query_headers(enterprise)
    rows = [query_row(), query_row(**{
        "交易时间": "2026-02-01 11:00:00", "交易流水号": "SYN-T2", "借贷标志": "贷",
        "交易金额": "2.03", "币种": "USD", "交易对方账卡号": "SYN-OTHER2"}),
        query_row(**{"交易时间": "2026-02-01 12:00:00", "交易流水号": "SYN-T3",
                     "查询帐号": "", "查询卡号": "SYN-FALLBACK", "交易金额": "1.01"}),
        query_row(**{"交易流水号": "SYN-T4", "查询帐号": "", "查询卡号": ""}),
        query_row(**{"交易流水号": "SYN-T5", "交易金额": "invalid-money"})]
    data = payload(headers, rows, suffix)
    view = preview(client, cid, data, "synthetic" + suffix)
    summary = view["table_summary"]
    assert view["automatic"] and summary["profile"] == profile
    assert (summary["record_total"], summary["accepted"], summary["unresolved"]) == (5, 3, 2)
    assert summary["currency_totals"] == {"CNY": "13.35", "USD": "2.03"}
    assert (view["mapping"]["self_account"], view["mapping"]["self_card"]) == ("查询帐号", "查询卡号")
    assert not {"self_name", "payer_name", "payee_name"}.intersection(view["mapping"])
    job = complete(client, cid, view)
    assert job["stage"] == "done" and (job["done"], job["total"], job["result"]["ok"]) == (5, 5, 3)
    before = facts(database, cid)
    assert [(row["payer_account"], row["payee_account"], row["amount"], row["currency"])
            for row in before] == [
        ("SYN-OWN-ACCOUNT", "SYN-OTHER", Decimal("12.34"), "CNY"),
        ("SYN-OTHER2", "SYN-OWN-ACCOUNT", Decimal("2.03"), "USD"),
        ("SYN-FALLBACK", "SYN-OTHER", Decimal("1.01"), "CNY")]
    assert before[0]["payer_name"] == before[1]["payee_name"] == before[2]["payer_name"] == ""
    records = source_records(client, cid, view["import_id"])
    assert [row["source_row"] for row in records] == [2, 3, 4, 5, 6]
    assert [row["disposition"] for row in records] == ["imported"] * 3 + ["unresolved"] * 2
    assert [row["details"]["source_account_column"] for row in records[:3]] == ["查询帐号", "查询帐号", "查询卡号"]
    assert records[3]["reason_code"] == "missing_identity"
    assert records[4]["reason_code"] == "invalid_amount"
    assert all(row["details"]["financial_effect"] == "undetermined" and row["flow_id"] is None
               for row in records[3:])
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == data
    other = make_case("Synthetic isolated owner")
    assert client.get(f"/api/v1/cases/{other}/sources/{view['import_id']}/file").status_code == 404
    assert complete(client, other, preview(client, other, data, "synthetic" + suffix))["result"]["ok"] == 3
    assert facts(database, other) == before
    repeated = complete(client, cid, preview(client, cid, data, "repeat" + suffix))
    assert (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 3)
    assert facts(database, cid) == before and coverage(client, cid)["unresolved_records"] == 2
    assert client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json()["result"] == job["result"]
    package = case_packages.export_package(cid)[0]
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert json.loads(archive.read("meta.json"))["format"] == 6
    restored = case_packages.import_package(package)["case_id"]
    assert facts(database, restored) == before and coverage(client, restored) == coverage(client, cid)
    restored_source = database.q_raw("select source_id,mapping_json,parser_version from import_sources "
                                    "where case_id=? and filename=?", [restored, "synthetic" + suffix])[0]
    assert restored_source["parser_version"] == profile
    assert json.loads(restored_source["mapping_json"])["self_account"] == "查询帐号"
    restored_records = source_records(client, restored, restored_source["source_id"])
    assert [{key: value for key, value in row.items() if key != "flow_id"} for row in restored_records] == [
        {key: value for key, value in row.items() if key != "flow_id"} for row in records]


@pytest.mark.parametrize("newest_first", [False, True])
@pytest.mark.parametrize("enterprise", [False, True])
def test_old_missing_owner_evaluation_stays_frozen_while_current_coverage_resolves(
        client, database, make_case, monkeypatch, newest_first, enterprise):
    cid = make_case("Synthetic resolved owner coverage")
    data = csv_bytes(query_headers(enterprise), [query_row()])
    old = profile_preview(client, cid, data, monkeypatch, "funds-trace-table-v13")
    assert old["table_summary"]["accepted"] == 0 and old["table_summary"]["unresolved"] == 1
    rechecked = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview", json={
        "mode": old["mode"], "mapping": old["mapping"]})
    assert rechecked.status_code == 200
    old = rechecked.json()
    assert old["table_summary"]["profile"] == "funds-trace-table-v13"
    current = preview(client, cid, data)
    assert current["table_summary"]["profile"] == NEW_PROFILE
    assert current["table_summary"]["accepted"] == 1
    first, second = (current, old) if newest_first else (old, current)
    first_job = complete(client, cid, first)
    assert first_job["stage"] == "done"
    first_tables = {table: database.q_raw(
        f"select * from {table} where case_id=? and source_id=? order by all", [cid, first["import_id"]])
        for table in ("import_sources", "import_members", "import_records", "flow_sources")}
    second_job = complete(client, cid, second)
    assert second_job["stage"] == "done"
    assert first_job["result"]["ok"] + second_job["result"]["ok"] == 1
    for table, values in first_tables.items():
        assert database.q_raw(f"select * from {table} where case_id=? and source_id=? order by all",
                              [cid, first["import_id"]]) == values
    assert client.get(f"/api/v1/cases/{cid}/imports/{first['import_id']}").json()["result"] == first_job["result"]
    old_record = source_records(client, cid, old["import_id"])[0]
    assert old_record["disposition"] == "unresolved"
    assert old_record["reason_code"] == ("unknown_table" if enterprise else "missing_identity")
    assert facts(database, cid)[0]["self_account"] == "SYN-OWN-ACCOUNT"
    assert coverage(client, cid)["unresolved_records"] == 0
    assert coverage(client, cid)["financial_interpretation_complete"]
    duplicate = complete(client, cid, preview(client, cid, data))
    assert (duplicate["result"]["ok"], duplicate["result"]["rows_dup"]) == (0, 1)
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == facts(database, cid)
    assert coverage(client, restored) == coverage(client, cid)


@pytest.mark.parametrize("newest_first", [False, True])
def test_new_owner_contract_cannot_rewrite_different_historical_facts(
        client, database, make_case, monkeypatch, newest_first):
    cid = make_case("Synthetic owner history conflict")
    data = csv_bytes(TRANSACTION_QUERY_ONLY, [query_row()])
    old = profile_preview(client, cid, data, monkeypatch, "funds-trace-table-v13")
    response = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview", json={
        "mode": "B", "mapping": {**old["mapping"], "self_account": "查询卡号", "trade_no": "交易流水号"}})
    assert response.status_code == 200
    old = response.json()
    current = preview(client, cid, data)
    assert old["table_summary"]["accepted"] == current["table_summary"]["accepted"] == 1
    first, second = (current, old) if newest_first else (old, current)
    first_job = complete(client, cid, first)
    assert first_job["stage"] == "done" and first_job["result"]["ok"] == 1
    before = facts(database, cid)
    assert before[0]["self_account"] == ("SYN-OWN-ACCOUNT" if newest_first else "SYN-OWN-CARD")
    report = source_records(client, cid, first["import_id"])
    second_job = complete(client, cid, second)
    assert second_job["stage"] == "done" and second_job["result"]["ok"] == 0
    assert second_job["result"]["table_summary"]["unresolved"] == 1
    assert facts(database, cid) == before
    assert source_records(client, cid, first["import_id"]) == report
    assert client.get(f"/api/v1/cases/{cid}/imports/{first['import_id']}").json()["result"] == first_job["result"]
    record = source_records(client, cid, second["import_id"])[0]
    assert record["disposition"] == "unresolved" and record["flow_id"] is None
    assert record["details"]["counterparty_basis"] == table_import.HISTORY_CONFLICT_BASIS
    assert coverage(client, cid)["unresolved_records"] == 1
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == before and coverage(client, restored) == coverage(client, cid)


def test_uniform_owner_conflict_cannot_be_hidden_by_repreview_or_frozen_assessment(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic owner candidate conflict")
    row = transaction(**{"查询帐号": "SYN-DIFFERENT-ACCOUNT", "查询卡号": "SYN-CARD"})
    view = preview(client, cid, csv_bytes(TRANSACTION_EXPLICIT, [row]))
    assert view["table_summary"]["unresolved"] == 1
    rechecked = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={
        "mode": "B", "mapping": {**view["mapping"], "self_account": "本方账号"}})
    assert rechecked.status_code == 200
    final = rechecked.json()
    assert final["table_summary"]["accepted"] == 0 and final["table_summary"]["unresolved"] == 1
    assert complete(client, cid, final)["result"]["ok"] == 0
    record = source_records(client, cid, view["import_id"])[0]
    assert record["reason_code"] == "conflicting_field" and record["flow_id"] is None
    old = profile_preview(client, cid, csv_bytes(TRANSACTION_EXPLICIT, [row]), monkeypatch,
                          "funds-trace-table-v13")
    assert complete(client, cid, old)["result"]["ok"] == 1
    assert facts(database, cid)[0]["self_account"] == "SYN-OWN"
    assert coverage(client, cid)["unresolved_records"] == 1
    assert not coverage(client, cid)["financial_interpretation_complete"]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == facts(database, cid)
    assert coverage(client, restored) == coverage(client, cid)


def test_missing_owner_is_not_filled_from_auxiliary_accounts_or_task_keys(client, database, make_case):
    cid = make_case("Synthetic no auxiliary owner inference")
    headers = query_headers(True)
    data = xlsx_bytes([
        ("Synthetic account", ACCOUNT, [{"账号": "SYN-AUX-ACCOUNT", "卡号": "SYN-AUX-CARD"}]),
        ("Synthetic transaction", headers, [query_row(**{
            "查询帐号": "", "查询卡号": "", "任务流水号": "SYN-TASK"})])])
    view = preview(client, cid, data, "synthetic.xlsx")
    assert (view["table_summary"]["accepted"], view["table_summary"]["unresolved"],
            view["table_summary"]["auxiliary"]) == (0, 1, 1)
    assert complete(client, cid, view)["result"]["ok"] == 0
    assert not facts(database, cid)
    records = source_records(client, cid, view["import_id"])
    assert [row["disposition"] for row in records] == ["auxiliary", "unresolved"]
    assert records[1]["reason_code"] == "missing_identity"


def test_v14_package_rejects_unknown_future_profile(client, make_case, tmp_path):
    cid = make_case("Synthetic uniform owner package future")
    view = preview(client, cid, csv_bytes(TRANSACTION_QUERY_ONLY, [query_row()]))
    assert complete(client, cid, view)["result"]["ok"] == 1
    members = _members(case_packages.export_package(cid)[0])
    _mutate(members, "import_sources", "update entries set parser_version='funds-trace-table-v18'", tmp_path)
    with pytest.raises(HTTPException):
        case_packages.import_package(_archive(members))


@pytest.mark.parametrize("profile,accepted", [("funds-trace-table-v13", 1), (NEW_PROFILE, 0)])
def test_uniform_contract_missing_owner_cannot_be_bypassed_by_switching_to_party_mode(
        client, database, make_case, monkeypatch, profile, accepted):
    cid = make_case("Synthetic missing owner A-mode boundary")
    data = csv_bytes(TRANSACTION_QUERY_ONLY, [query_row(**{"查询帐号": "", "查询卡号": ""})])
    view = profile_preview(client, cid, data, monkeypatch, profile)
    assert view["table_summary"]["accepted"] == 0
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={
        "mode": "A", "mapping": {**view["mapping"], "payer_account": "交易流水号",
                                  "payee_account": "交易对方账卡号"}})
    assert response.status_code == 200
    final = response.json()
    assert final["table_summary"]["accepted"] == accepted
    assert complete(client, cid, final)["result"]["ok"] == accepted
    assert len(facts(database, cid)) == accepted
    if not accepted:
        record = source_records(client, cid, view["import_id"])[0]
        assert record["reason_code"] == "missing_identity" and record["flow_id"] is None


@pytest.mark.parametrize("direction,own_side,counter_side", [
    ("借", "payer_account", "payee_account"), ("贷", "payee_account", "payer_account"),
])
@pytest.mark.parametrize("owner_column,query_account,expected", [
    ("查询帐号", "SYN-OWN-ACCOUNT", "SYN-OWN-ACCOUNT"),
    ("查询卡号", "SYN-OWN-ACCOUNT", "SYN-OWN-ACCOUNT"),
    ("查询卡号", "", "SYN-OWN-CARD"),
])
def test_uniform_contract_preserves_valid_directed_party_projection(
        client, database, make_case, direction, own_side, counter_side, owner_column, query_account, expected):
    cid = make_case("Synthetic valid A and B source roles")
    data = csv_bytes(TRANSACTION_QUERY_ONLY, [query_row(**{"借贷标志": direction, "查询帐号": query_account})])
    view = preview(client, cid, data)
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={
        "mode": "A", "mapping": {**view["mapping"], own_side: owner_column,
                                  counter_side: "交易对方账卡号"}})
    assert response.status_code == 200
    final = response.json()
    assert final["table_summary"]["accepted"] == 1
    assert complete(client, cid, final)["result"]["ok"] == 1
    row = facts(database, cid)[0]
    assert row[own_side] == row["self_account"] == expected
    assert row[counter_side] == "SYN-OTHER"
