"""Synthetic source postings, complete CSV structure and historical replay."""
import csv
import copy
import io
import json
import zipfile

import pytest

from app import table_archive, table_import
from app.services import imports
from test_folder_evidence import complete

HEADERS = "交易卡号 交易账号 交易方户名 交易方证件号码 交易时间 交易金额 交易余额 收付标志 交易对手账卡号 现金标志 对手户名 对手身份证号 对手开户银行 摘要说明 交易币种 交易网点名称 交易发生地 交易是否成功 传票号 IP地址 MAC地址 对手交易余额 交易流水号 日志号 凭证种类 凭证号 交易柜员号 备注 商户名称 交易类型 查询反馈结果原因".split()


def payload(delimiter=",", changes=None, unknown=False):
    rows = []
    for number, stamp, amount, balance, other, branch in [
        ("SYN-P1", "2036-08-22 09:11:12", "17.00", "983.00", "SYN-B", "SYN-BR1"),
        ("SYN-P2", "2036-08-22 09:16:12", "17.00", "966.00", "SYN-B", "SYN-BR1"),
        ("SYN-P1", "2036-08-22 09:11:12", "17.00", "983.00", "SYN-B", "SYN-BR2"),
        ("SYN-P3", "2036-08-22 09:22:00", "5.31", "960.69", "", "SYN-BR1"),
    ]:
        row = {"交易卡号": "SYN-CARD", "交易账号": "SYN-A", "交易方户名": "SYN-ALPHA",
            "交易时间": stamp, "交易金额": amount, "交易余额": balance, "收付标志": "出",
            "交易对手账卡号": other, "对手户名": "SYN-BETA", "对手开户银行": "SYN-BANK",
            "摘要说明": 'alpha\t,beta;quoted "evidence"', "交易币种": "CNY",
            "交易流水号": number, "凭证号": "SYN-VOUCHER", "交易网点名称": branch}
        row.update(changes or {})
        rows.append([row.get(h, "") for h in HEADERS])
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=delimiter, quoting=csv.QUOTE_ALL)
    headers = ["unrecognized" if h == "查询反馈结果原因" else h for h in HEADERS] if unknown else HEADERS
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def preview(client, cid, data, name="source.csv"):
    result = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": (name, data)})
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.parametrize("summary", ['  SYN original, \"summary\"\nline  ', ""])
@pytest.mark.parametrize("archive", [False, True])
def test_closed_summary_is_its_own_source_column_and_remark_remains_evidence(client, make_case, summary, archive):
    cid = make_case("SYN separate source text")
    original = payload(changes={"摘要说明": summary, "备注": "SYN independent remark"})
    data, name = original, "source.csv"
    if archive:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as package:
            package.writestr("source.csv", original)
        data, name = stream.getvalue(), "source.zip"
    view = preview(client, cid, data, name)
    assert view["stats"]["ok"] == 4
    assert complete(client, cid, view)["stage"] == "done"
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert len(rows) == 3 and all(row["summary"] == summary for row in rows)
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    for record in records:
        evidence = json.loads(record["details"]["source_field_evidence"])
        assert {(item["field"], item["column"], item["raw"]) for item in evidence if item["field"] in {"summary", "remark"}} == {
            ("summary", "摘要说明", summary), ("remark", "备注", "SYN independent remark")}
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == data
    packed = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("source.fundcase", packed.content)})
    assert restored.status_code == 200, restored.text
    restored_rows = client.get(f"/api/v1/cases/{restored.json()['case_id']}/flows").json()["items"]
    assert len(restored_rows) == 3 and all(row["summary"] == summary for row in restored_rows)


def test_old_source_profile_keeps_joined_text_replay():
    data = payload(changes={"摘要说明": "SYN original", "备注": "SYN remark"})
    plan = table_import.parse(io.BytesIO(data), suffix=".csv", profile=table_import.SOURCE_CONTRACT_PROFILE)
    assert len(plan["rows"]) == 4 and all(row["summary"] == "SYN original；SYN remark" for row in plan["rows"])
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as package:
        package.writestr("source.csv", data)
    archived = table_archive.parse(io.BytesIO(stream.getvalue()), profile=table_archive.SOURCE_CONTRACT_PROFILE)
    assert len(archived["rows"]) == 4 and all(row["summary"] == "SYN original；SYN remark" for row in archived["rows"])


def test_old_ready_source_session_keeps_its_version_and_receipt_after_text_upgrade(client, make_case, monkeypatch):
    cid = make_case("SYN frozen text interpretation")
    with monkeypatch.context() as historical:
        historical.setattr(imports, "TABLE_PROFILE", table_import.SOURCE_CONTRACT_PROFILE)
        view = preview(client, cid, payload(changes={"摘要说明": "SYN original", "备注": "SYN remark"}))
    assert complete(client, cid, view)["stage"] == "done"
    receipt = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json()
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert len(rows) == 3 and all(row["summary"] == "SYN original；SYN remark" for row in rows)
    assert complete(client, cid, view)["stage"] == "done"
    assert client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json() == receipt


@pytest.mark.parametrize("suffix,archive,profile", [
    (".xlsx", False, "funds-trace-table-v66"),
    (".xls", False, "funds-trace-table-v67"),
    (".xls", True, "funds-trace-table-archive-v59"),
])
def test_source_text_version_routes_workbooks_without_merging_remark(client, make_case, suffix, archive, profile):
    from test_table_multisheet_append import encoded
    cid = make_case("SYN workbook source text")
    cells = list(csv.reader(io.StringIO(payload(changes={"摘要说明": "", "备注": "SYN remark"}).decode("utf-8-sig"))))[:2]
    if suffix == ".xls":
        from xls_fixtures import workbook
        data = workbook([("SYN source", cells)])
    else:
        data = encoded([("SYN source", cells)], suffix)
    name = "source" + suffix
    if archive:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as package:
            package.writestr(name, data)
        data, name = stream.getvalue(), "source.zip"
    view = preview(client, cid, data, name)
    assert view["table_summary"]["profile"] == profile and view["stats"]["ok"] == 1
    assert complete(client, cid, view)["stage"] == "done"
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert len(rows) == 1 and rows[0]["summary"] == "" and rows[0]["trade_no"] == "SYN-P1"
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    assert any(item["field"] == "remark" and item["raw"] == "SYN remark" for item in json.loads(records[0]["details"]["source_field_evidence"]))


def test_placeholder_primary_reference_stays_raw_and_never_becomes_an_identifier(client, make_case):
    cid = make_case("SYN missing reference")
    view = preview(client, cid, payload(changes={"交易流水号": "-", "凭证号": ""}))
    assert view["stats"]["ok"] == 4
    assert complete(client, cid, view)["stage"] == "done"
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert len(rows) == 3 and all(row["trade_no"] == "" for row in rows)
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    assert len(records) == 4
    for record in records:
        assert record["details"]["trade_no_basis"] == "none"
        evidence = json.loads(record["details"]["source_field_evidence"])
        assert any(item["role"] == "source_transaction_id" and item["raw"] == "-" for item in evidence)


def test_full_primary_reference_filters_legacy_and_react_queries_as_strings(client, make_case):
    cid = make_case("SYN full keyword")
    number = "00090071992547409931234567890123456789"
    view = preview(client, cid, payload(changes={"交易流水号": number}))
    assert complete(client, cid, view)["stage"] == "done"
    for path, params, key in [("/api/flows", {"case_id": cid, "kw": number, "granularity": "events"}, "rows"),
                              (f"/api/v1/cases/{cid}/flows", {"kw": number}, "items")]:
        result = client.get(path, params=params)
        assert result.status_code == 200, result.text
        assert result.json()[key] and all(row["trade_no"] == number for row in result.json()[key])
    assert client.get("/api/flows", params={"case_id": cid, "kw": number + "9"}).json()["total"] == 0
    assert client.get(f"/api/v1/cases/{cid}/flows", params={"kw": number + "9"}).json()["total"] == 0


@pytest.mark.parametrize("delimiter", [",", "\t", ";"])
def test_complete_source_postings_keep_roles_originals_and_two_supports(client, make_case, delimiter):
    cid = make_case("SYN source contract")
    data = payload(delimiter)
    view = preview(client, cid, data)
    assert view["stats"]["ok"] == 4
    assert view["table_summary"]["documents"][0]["reference_profile"] == "bank-feedback31-reference-v1"
    job = complete(client, cid, view)
    assert job["stage"] == "done" and (job["result"]["ok"], job["result"]["rows_dup"]) == (3, 1)
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert {r["trade_no"] for r in rows} == {"SYN-P1", "SYN-P2", "SYN-P3"}
    assert all(r["payer_name"] == "SYN-ALPHA" and r["payee_bank"] == "SYN-BANK" for r in rows)
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["amount_total"] == "39.31"
    first = next(r for r in rows if r["trade_no"] == "SYN-P1")
    assert len(client.get(f"/api/v1/cases/{cid}/flows/{first['id']}/sources").json()["items"]) == 2
    original = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file")
    assert original.content == data
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    assert all(r["details"]["reported_status"] == "unknown" for r in records)
    evidence = json.loads(records[0]["details"]["source_field_evidence"])
    assert {(x["role"], x["raw"]) for x in evidence if x["field"] == "trade_no"} == {
        ("source_transaction_id", "SYN-P1"), ("independent_voucher_reference", "SYN-VOUCHER"),
        ("source_slip_reference", ""), ("source_log_reference", "")}


@pytest.mark.parametrize("changes,unknown", [({"交易是否成功": "失败"}, False), ({}, True), ({"交易余额": ""}, False)])
def test_closed_source_status_rule_never_infers_success_without_ledger_facts(client, make_case, changes, unknown):
    cid = make_case("SYN rejected source status")
    view = preview(client, cid, payload(changes=changes, unknown=unknown))
    assert view["stats"]["ok"] == 0
    assert complete(client, cid, view)["stage"] == "done"
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0


def test_old_ready_source_and_new_explicit_reimport_keep_receipt_and_supports(client, make_case, monkeypatch):
    cid = make_case("SYN explicit source upgrade")
    data = payload()
    with monkeypatch.context() as old:
        old.setattr(imports, "TABLE_PROFILE", table_import.ZONED_PROFILE)
        prior = preview(client, cid, data)
    assert prior["stats"]["ok"] == 0
    prior_job = complete(client, cid, prior)
    receipt = client.get(f"/api/v1/cases/{cid}/imports/{prior['import_id']}").json()
    first = complete(client, cid, preview(client, cid, data))
    assert first["stage"] == "done" and first["result"]["ok"] == 3
    second = complete(client, cid, preview(client, cid, data))
    assert second["stage"] == "done" and (second["result"]["ok"], second["result"]["rows_dup"]) == (0, 4)
    assert client.get(f"/api/v1/cases/{cid}/imports/{prior['import_id']}").json() == receipt
    assert prior_job["result"]["ok"] == 0
    packed = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("source.fundcase", packed.content)})
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/v1/cases/{restored.json()['case_id']}/stats").json()["amount_total"] == "39.31"
    assert client.delete(f"/api/batches/{first['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 3
    assert client.delete(f"/api/batches/{second['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0


def test_archive_posting_supports_repeat_and_restore_without_changing_positions(client, make_case):
    cid = make_case("SYN archive source postings")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("member.csv", payload())
    data = stream.getvalue()
    first_view = preview(client, cid, data, "source.zip")
    first = complete(client, cid, first_view)
    assert first["stage"] == "done" and (first["result"]["ok"], first["result"]["rows_dup"]) == (3, 1)
    records = client.get(f"/api/v1/cases/{cid}/imports/{first_view['import_id']}/records").json()["items"]
    assert [r["source_row"] for r in records] == [2, 3, 4, 5]
    assert records[0]["flow_id"] == records[2]["flow_id"] != records[1]["flow_id"]
    assert len({r["record_key"] for r in records}) == 4
    second = complete(client, cid, preview(client, cid, data, "source.zip"))
    assert second["stage"] == "done" and (second["result"]["ok"], second["result"]["rows_dup"]) == (0, 4)
    packed = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("source.fundcase", packed.content)})
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/v1/cases/{restored.json()['case_id']}/stats").json()["amount_total"] == "39.31"


@pytest.mark.parametrize("mixed,count,amount", [(False, 3, "13.68"), (True, 4, "23.72")])
def test_current_source_profiles_keep_the_frozen_zoned_and_mixed_totals(client, make_case, mixed, count, amount):
    rows = [
        ("2026-02-03T00:10:00.123456+08:00", "1.23", "SYN-Z1"),
        ("2026-02-02T16:10:00.123456Z", "4.56", "SYN-Z2"),
        ("2026-02-02T12:00:00.000001-05:00", "7.89", "SYN-Z3"),
    ]
    if mixed:
        rows += [("2026-02-03 00:10:00.123456", "10.04", "SYN-M4"),
                 ("2026-02-03T00:10:00+25:00", "99.00", "SYN-M5")]
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["交易时间", "付款账号", "收款账号", "交易金额", "币种", "交易流水号"])
    writer.writerows((stamp, "SYN-ZA", "SYN-ZB", money, "CNY", number) for stamp, money, number in rows)
    cid = make_case("SYN zoned source compatibility")
    job = complete(client, cid, preview(client, cid, output.getvalue().encode("utf-8-sig")))
    assert job["stage"] == "done" and job["result"]["ok"] == count
    stats = client.get(f"/api/v1/cases/{cid}/stats").json()
    assert stats["flows"] == count and stats["amount_total"] == amount
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert {r["trade_time"] for r in flows} == {stamp for stamp, _, _ in rows[:count]}
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [r["id"] for r in flows]}).status_code == 200
    edge = client.get("/api/graph", params={"case_id": cid}).json()["edges"]
    assert len(edge) == 1 and edge[0]["cnt"] == count and edge[0]["total"] == amount


@pytest.mark.parametrize("profile", sorted(table_archive.SUPPORTED_PROFILES & table_import.SOURCE_CONTRACT_PROFILES))
def test_archive_decodes_each_same_profile_csv_member_once(monkeypatch, profile):
    data = payload()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as package:
        package.writestr("first.csv", data)
        package.writestr("second.csv", data)
    original, calls = table_import._csv, []
    def observed(raw, suffix, selected):
        calls.append((raw, suffix, selected))
        return original(raw, suffix, selected)
    monkeypatch.setattr(table_import, "_csv", observed)
    plan = table_archive.parse(io.BytesIO(stream.getvalue()), profile=profile)
    try:
        # Equal bytes in separate members retain independent source records.
        assert calls == [(data, ".csv", profile), (data, ".csv", profile)]
        assert len(plan["records"]) == 8
        assert [r["member_path"] for r in plan["records"]] == ["first.csv"] * 4 + ["second.csv"] * 4
        assert [r["source_row"] for r in plan["records"]] == [2, 3, 4, 5] * 2
    finally:
        if plan.get("_details_pool") is not None:
            plan["_details_pool"].close()


@pytest.mark.parametrize("profile", [table_archive.SOURCE_CONTRACT_PROFILE, table_archive.PROFILE,
                                    table_archive.SOURCE_OBSERVATION_EVIDENCE_PROFILE])
@pytest.mark.parametrize("kind", ["cancellation", "duplicate-bytes", "auxiliary-only", "nested"])
def test_archive_csv_reuse_matches_fresh_decode_for_every_plan_field(monkeypatch, profile, kind):
    from test_archive_binding_once import members
    from test_folder_evidence import archive
    data = archive(members(kind))
    def freeze():
        if kind == "nested" and profile == table_archive.SOURCE_CONTRACT_PROFILE:
            with pytest.raises(table_import.TableImportError, match="需使用原规则") as caught:
                table_archive.parse(io.BytesIO(data), profile=profile)
            return str(caught.value)
        plan = table_archive.parse(io.BytesIO(data), profile=profile)
        try:
            return copy.deepcopy({key: value for key, value in plan.items() if key != "_details_pool"})
        finally:
            if plan.get("_details_pool") is not None:
                plan["_details_pool"].close()
    actual = freeze()
    original = table_import.parse
    def fresh_decode(*args, **kwargs):
        kwargs.pop("_decoded_csv", None)
        return original(*args, **kwargs)
    monkeypatch.setattr(table_import, "parse", fresh_decode)
    assert freeze() == actual


def test_auxiliary_csv_keeps_its_specialized_interpreter(monkeypatch):
    text = io.StringIO(newline="")
    writer = csv.writer(text, delimiter="\t")
    writer.writerow(table_archive.AUXILIARY_SCHEMAS["freeze"])
    writer.writerow(["SYN"] * len(table_archive.AUXILIARY_SCHEMAS["freeze"]))
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as package:
        package.writestr("freeze.csv", text.getvalue().encode("utf-8-sig"))
    def unexpected(*args, **kwargs):
        pytest.fail("auxiliary input must retain its specialized parser")
    monkeypatch.setattr(table_import, "parse", unexpected)
    plan = table_archive.parse(io.BytesIO(stream.getvalue()))
    assert plan["rows"] == []
    assert len(plan["records"]) == 1
    assert plan["records"][0]["disposition"] == "auxiliary"
    assert plan["records"][0]["details"]["source_role"] == "freeze"


@pytest.mark.parametrize("profile", [table_archive.IDENTITY_PROFILE, table_archive.REFERENCE_PROFILE])
def test_historical_different_member_profile_decodes_normally(monkeypatch, profile):
    data = b"date,payer_account,payee_account,amount\n2026-01-01,0001,0002,2.34\n"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as package:
        package.writestr("source.csv", data)
    original, selected = table_import.parse, []
    def observed(*args, **kwargs):
        assert kwargs.get("_decoded_csv") is None
        selected.append(kwargs["profile"])
        return original(*args, **kwargs)
    monkeypatch.setattr(table_import, "parse", observed)
    plan = table_archive.parse(io.BytesIO(stream.getvalue()), profile=profile)
    assert selected == [table_import.IDENTITY_PROFILE]
    assert len(plan["rows"]) == 1
    assert plan["rows"][0]["payer_account"] == "0001"
    assert str(plan["rows"][0]["amount"]) == "2.34"


@pytest.mark.parametrize("mismatch", ["bytes", "suffix", "profile", "type"])
def test_decoded_csv_cannot_cross_source_or_profile_binding(mismatch):
    data, profile = payload(), table_archive.SOURCE_OBSERVATION_EVIDENCE_PROFILE
    decoded = table_import._DecodedCSV(data, ".csv", profile)
    options = dict(suffix=".csv", profile=profile, reference_policy=table_import.TRANSACTION_REFERENCE_POLICY,
                   _decoded_csv=decoded)
    if mismatch == "bytes":
        data += b"\r\n"
    elif mismatch == "suffix":
        options["suffix"] = ".txt"
    elif mismatch == "profile":
        options["profile"] = table_archive.SOURCE_TEXT_PROFILE
    else:
        options["_decoded_csv"] = {"rows": []}
    with pytest.raises(table_import.TableImportError, match="临时CSV解码与来源或规则不匹配"):
        table_import.parse(data, **options)


def test_decoded_csv_is_consumed_once_and_still_checks_source_size(monkeypatch):
    data, profile = payload(), table_archive.PROFILE
    decoded = table_import._DecodedCSV(data, ".csv", profile)
    options = dict(suffix=".csv", profile=profile, reference_policy=table_import.TRANSACTION_REFERENCE_POLICY,
                   _decoded_csv=decoded)
    with monkeypatch.context() as limited:
        limited.setattr(table_import, "MAX_BYTES", len(data)-1)
        with pytest.raises(table_import.TableImportError, match="超出安全处理大小"):
            table_import.parse(io.BytesIO(data), **options)
    plan = table_import.parse(io.BytesIO(data), **options)
    assert len(plan["records"]) == 4
    assert decoded._raw is decoded._sheets is decoded.header is None
    with pytest.raises(table_import.TableImportError, match="临时CSV解码与来源或规则不匹配"):
        table_import.parse(data, **options)


@pytest.mark.parametrize("owner,limit,value,message", [
    (table_archive, "MAX_MEMBER_BYTES", -1, "单成员"),
    (table_archive, "MAX_EXPANDED_BYTES", -1, "累计展开量"),
    (table_import, "MAX_BYTES", -1, "表格文件超出"),
    (table_import, "MAX_RECORDS", 4, "记录数或列数"),
    (table_import, "MAX_COLUMNS", len(HEADERS)-1, "记录数或列数"),
    (table_import, "MAX_FIELD", 8, "字段超出"),
])
def test_archive_csv_reuse_preserves_member_and_table_limits(monkeypatch, owner, limit, value, message):
    data = payload()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as package:
        package.writestr("source.csv", data)
    monkeypatch.setattr(owner, limit, len(data)-1 if value == -1 else value)
    with pytest.raises(table_import.TableImportError, match=message):
        table_archive.parse(io.BytesIO(stream.getvalue()))
