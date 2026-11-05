"""Synthetic automatic-table journals preserve their facts and closed dispositions."""
import hashlib
import csv
import io
import json
import uuid
import zipfile

from fastapi import HTTPException
import pytest

from app import case_packages, table_import
from test_feedback_package import _archive, _journal, _members, _mutate, _seed as _seed_feedback
from test_feedback_import_api import complete


def _source_auxiliary_package(client, make_case, monkeypatch, profile):
    from app import table_archive
    from test_source_contract_68 import payload
    headers = {
        "account": "账户开户名称 开户人证件号码 交易卡号 交易账号 账号开户时间 账户余额 可用余额 币种 开户网点代码 开户网点 账户状态 钞汇标志名称 开户人证件类型 销户日期 账户类型 开户联系方式 通信地址 联系电话 代理人 代理人电话 备注 开户省份 开户城市 账号开户银行 客户代码 法人代表 客户工商执照号码 法人代表证件号码 住宅地址 邮政编码 代办人证件号码 邮箱地址 关联资金账户 地税纳税号 单位电话 代办人证件类型 住宅电话 法人代表证件类型 国税纳税号 单位地址 工作单位 销户网点 最后交易时间 账户销户银行 任务流水号",
        "freeze": "银行名称 账号 冻结措施类型 冻结措施类型名称 冻结金额 冻结机关 冻结开始日期 冻结截止日期 措施序号 备注 任务流水号 查控主体类别",
        "client": "开户名称 证照类型 证照号码 联系电话",
        "subaccount": "银行名称 开户账号 子账户账号 余额 可用余额 子账户类别 子账户序号 币种 钞汇标识 账户状态 账户人姓名 账户序号 备注",
        "manifest": "任务流水号 银行名称 主体类别 证账号码 账卡号 发送时间 反馈时间 反馈结果 反馈非明细结果 反馈明细结果 入库时间 入库状态 请求单号 查询结果",
    }
    members = {"transactions.csv": payload()}
    for role, names in headers.items():
        columns = names.split()
        stream = io.StringIO(newline="")
        csv.writer(stream).writerows([columns, ["SYN-AUX-" + role, *([""] * (len(columns) - 1))]])
        members[role + ".csv"] = stream.getvalue().encode("utf-8-sig")
    cid = make_case("SYN auxiliary source package")
    with monkeypatch.context() as source_profile:
        source_profile.setattr(table_archive, "PROFILE", profile)
        source_profile.setattr(table_archive, "evidence_profile", lambda source: profile)
        view = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("source.zip", _archive(members))})
    assert view.status_code == 200, view.text
    view = view.json()
    assert view["table_summary"]["profile"] == profile
    assert view["table_summary"]["auxiliary"] == 5
    assert complete(client, cid, view)["stage"] == "done"
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    auxiliary = [record for record in records if record["disposition"] == "auxiliary"]
    assert len(auxiliary) == 5 and {record["details"]["source_role"] for record in auxiliary} == set(headers)
    assert all(record["flow_id"] is None and record["details"]["financial_effect"] == "not_a_flow"
               and not {"source_contract", "contract_sha256", "amount_model", "candidate_scope", "direction_basis", "balance_basis"}.intersection(record["details"])
               for record in auxiliary)
    package = client.get(f"/api/cases/{cid}/export")
    assert package.status_code == 200, package.text
    return cid, records, package.content


@pytest.mark.parametrize("profile", ["funds-trace-table-archive-v30", "funds-trace-table-archive-v31",
                                     "funds-trace-table-archive-v33", "funds-trace-table-archive-v34"])
def test_public_package_restores_source_auxiliary_without_inventing_financial_contract(client, make_case, monkeypatch, profile):
    cid, records, content = _source_auxiliary_package(client, make_case, monkeypatch, profile)
    restored = client.post("/api/cases/import", files={"file": ("source.fundcase", content)})
    assert restored.status_code == 200, restored.text
    new_case = restored.json()["case_id"]
    assert client.get(f"/api/v1/cases/{new_case}/stats").json()["amount_total"] == "39.31"
    flows = client.get(f"/api/v1/cases/{new_case}/flows").json()
    assert flows["total"] == 3
    sources = client.get(f"/api/v1/cases/{new_case}/flows/{flows['items'][0]['id']}/sources").json()["items"]
    source_id = sources[0]["source_id"]
    assert {source["source_id"] for source in sources} == {source_id}
    imported = client.get(f"/api/v1/cases/{new_case}/imports/{source_id}/records").json()["items"]
    assert [(r["member_path"], r["source_row"], r["disposition"], r["reason_code"], r["details"]) for r in imported] == [
        (r["member_path"], r["source_row"], r["disposition"], r["reason_code"], r["details"]) for r in records]


@pytest.mark.parametrize("mutation", ["table_missing_contract", "aux_partial_contract", "aux_invalid_contract",
                                     "aux_bad_source_role", "aux_claims_transaction", "aux_member_role", "aux_flow_link"])
def test_public_package_rejects_tampered_auxiliary_and_missing_table_contract_without_partial_case(
        client, database, make_case, monkeypatch, tmp_path, mutation):
    _, records, content = _source_auxiliary_package(client, make_case, monkeypatch, "funds-trace-table-archive-v33")
    members = _members(content)
    if mutation == "aux_member_role":
        table, sql = "import_members", "update entries set role='table' where role='auxiliary'"
    elif mutation == "aux_flow_link":
        position = next(record["record_no"] for record in records if record["disposition"] == "auxiliary")
        table, sql = "flow_sources", f"insert into entries select source_id,{position},flow_id,'imported' from entries limit 1"
    else:
        table = "import_records"
        sql = {
            "table_missing_contract": "update entries set details_json=cast(json_merge_patch(details_json,'{\"source_contract\":null,\"contract_sha256\":null,\"amount_model\":null,\"candidate_scope\":null,\"direction_basis\":null,\"balance_basis\":null}') as varchar) where disposition in ('imported','duplicate')",
            "aux_partial_contract": "update entries set details_json=cast(json_merge_patch(details_json,'{\"source_contract\":\"funds-trace-source-contract-v1\"}') as varchar) where disposition='auxiliary'",
            "aux_invalid_contract": "update entries set details_json=cast(json_merge_patch(details_json,'{\"source_contract\":\"funds-trace-source-contract-v1\",\"contract_sha256\":\"invalid\",\"amount_model\":\"legacy_magnitude\",\"candidate_scope\":\"all_region_candidates\"}') as varchar) where disposition='auxiliary'",
            "aux_bad_source_role": "update entries set details_json=cast(json_merge_patch(details_json,'{\"source_role\":\"transaction\"}') as varchar) where disposition='auxiliary'",
            "aux_claims_transaction": "update entries set disposition='imported',reason_code='transaction_valid' where disposition='auxiliary'",
        }[mutation]
    _mutate(members, table, sql, tmp_path)
    before = database.q("select count(*) as n from cases")[0]["n"]
    response = client.post("/api/cases/import", files={"file": ("tampered.fundcase", _archive(members))})
    assert response.status_code == 400, response.text
    assert database.q("select count(*) as n from cases")[0]["n"] == before


def _seed_table(database, make_case, flow_row, *, case_id=None, mapping=None,
                profile="funds-trace-table-v5", filename="synthetic-table.csv", sheet=""):
    case_id = case_id or make_case("Synthetic automatic table package")
    source_id = uuid.uuid4().hex
    row = flow_row(amount="12.34", balance="56.78")
    columns = ",".join(row)
    flow_id = database.q(f"insert into flows(case_id,{columns}) values (?,{','.join('?' for _ in row)}) returning id",
                         [case_id, *row.values()])[0]["id"]
    flow = database.q("select * from flows where id=?", [flow_id])[0]
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,
        mapping_json,sheet,parser_version,row_kind) values (?,?,?,?,100,'B',?,?,?,'table_record')""",
               [source_id, case_id, filename, "a" * 64, json.dumps(mapping or {}), sheet, profile])
    paths = ("table.csv", "auxiliary.csv")
    for path, role, count in zip(paths, ("table", "auxiliary"), (6, 1)):
        digest = hashlib.sha256(path.encode()).hexdigest()
        database.x("""insert into import_members(case_id,source_id,member_path,role,sha256,
            size_bytes,encoding,row_count) values (?,?,?,?,?,100,'utf-8-sig',?)""",
                   [case_id, source_id, path, role, digest, count])
    entries = [(1, paths[0], 1, "auxiliary", "preamble"),
               (2, paths[0], 3, "imported", "transaction_valid"),
               (3, paths[0], 4, "duplicate", "transaction_valid"),
               (4, paths[0], 5, "unresolved", "conflicting_amount"),
               (5, paths[0], 6, "non_monetary", "zero_amount"),
               (6, paths[0], 7, "non_transaction", "blank_record"),
               (7, paths[1], 2, "auxiliary", "auxiliary_table")]
    for number, path, source_row, disposition, reason in entries:
        effect = ("source_reported" if disposition in ("imported", "duplicate") else
                  "undetermined" if disposition == "unresolved" else
                  "no_movement" if disposition == "non_monetary" else "not_a_flow")
        details = {"source_row": str(source_row), "financial_effect": effect}
        if disposition in ("imported", "duplicate"):
            details["flow_sha256"] = table_import.flow_digest(flow)
        elif disposition == "unresolved":
            details["validation_issues"] = reason
        if reason == "auxiliary_table":
            details["source_role"] = "account"
        member_sha = hashlib.sha256(path.encode()).hexdigest()
        database.x("""insert into import_records(case_id,source_id,record_no,member_path,source_row,
            record_key,disposition,reason_code,details_json) values (?,?,?,?,?,?,?,?,?)""",
                   [case_id, source_id, number, path, source_row,
                    table_import.record_key(path, member_sha, source_row), disposition,
                    reason, json.dumps(details)])
        if disposition in ("imported", "duplicate"):
            database.x("insert into flow_sources(case_id,source_id,source_row,flow_id,disposition) values (?,?,?,?,?)",
                       [case_id, source_id, number, flow_id, disposition])
    return case_id, source_id, flow_id


@pytest.mark.parametrize("profile,filename,sheet", [
    ("funds-trace-table-v5", "synthetic-table.csv", ""),
    ("funds-trace-table-v5", "synthetic-workbook.xlsx", "workbook"),
    ("funds-trace-table-archive-v1", "synthetic-nested.zip", ""),
])
def test_table_journal_format4_round_trip_without_source_files(database, make_case, flow_row, profile, filename, sheet):
    case_id, old_source, old_flow = _seed_table(database, make_case, flow_row,
                                              profile=profile, filename=filename, sheet=sheet)
    content = case_packages.export_package(case_id)[0]
    members = _members(content)
    assert json.loads(members["meta.json"])["format"] == 4
    assert json.loads(members["meta.json"])["source_files"] == "not_included"
    assert all(not path.endswith((".csv", ".xlsx", ".zip")) for path in members)
    new_case = case_packages.import_package(content)["case_id"]
    for table in case_packages.JOURNAL_PKG_COLS:
        assert _journal(database, table, case_id) == _journal(database, table, new_case)
    source = database.q("select source_id,origin,mapping_json from import_sources where case_id=?", [new_case])[0]
    assert source["source_id"] != old_source
    assert source["origin"] == "package"
    assert json.loads(source["mapping_json"]) == {}
    links = database.q("select source_row,flow_id,disposition from flow_sources where case_id=? order by source_row", [new_case])
    assert [(link["source_row"], link["disposition"]) for link in links] == [(2, "imported"), (3, "duplicate")]
    assert len({link["flow_id"] for link in links}) == 1
    assert links[0]["flow_id"] != old_flow


@pytest.mark.parametrize("kind", ["csv", "xlsx", "nested_zip"])
def test_public_automatic_table_import_then_package_round_trip(client, database, make_case, kind):
    matrix = [["Transaction Date", "Account Number", "Account Name", "Counterparty Account",
               "Credit/Debit", "Transaction Amount (cents)", "Currency"],
              ["2026-01-01 10:00:00", "SYN-A", "Synthetic own name", "SYN-B", "debit", "1234", "CNY"],
              ["2026-01-01 10:00:01", "SYN-A", "Synthetic own name", "SYN-B", "unknown", "999", "USD"]]
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(matrix)
    payload = stream.getvalue().encode()
    filename = "synthetic.csv"
    if kind == "xlsx":
        from openpyxl import Workbook
        workbook = Workbook()
        for row in matrix:
            workbook.active.append(row)
        binary = io.BytesIO()
        workbook.save(binary)
        workbook.close()
        payload, filename = binary.getvalue(), "synthetic.xlsx"
    elif kind == "nested_zip":
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("synthetic.csv", payload)
        outer = io.BytesIO()
        with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("nested.zip", inner.getvalue())
        payload, filename = outer.getvalue(), "synthetic.zip"
    case_id = make_case("Public automatic-table package")
    response = client.post(f"/api/v1/cases/{case_id}/imports/preview", files={"file": (filename, payload)})
    assert response.status_code == 200, response.text
    job = complete(client, case_id, response.json())
    assert job["stage"] == "done", job
    assert job["result"]["ok"] == 1
    content = case_packages.export_package(case_id)[0]
    assert json.loads(_members(content)["meta.json"])["format"] == 6
    restored = case_packages.import_package(content)["case_id"]
    fields = ",".join(table_import.FLOW_FIELDS)
    assert database.q(f"select {fields} from flows where case_id=?", [restored]) == database.q(
        f"select {fields} from flows where case_id=?", [case_id])
    assert database.q("select disposition,reason_code from import_records where case_id=? order by record_no", [restored]) == [
        {"disposition": "imported", "reason_code": "transaction_valid"},
        {"disposition": "unresolved", "reason_code": "invalid_direction"}]
    for table in case_packages.JOURNAL_PKG_COLS:
        assert _journal(database, table, restored) == _journal(database, table, case_id)


@pytest.mark.parametrize("profile", ["funds-trace-bank-feedback-v1", "funds-trace-bank-feedback-v2", "funds-trace-bank-feedback-v3"])
def test_feedback_and_table_journals_are_partitioned_and_both_validated(database, make_case, flow_row, profile):
    case_id, _, _ = _seed_feedback(database, make_case, flow_row, profile=profile)
    _seed_table(database, make_case, flow_row, case_id=case_id)
    content = case_packages.export_package(case_id)[0]
    assert json.loads(_members(content)["meta.json"])["format"] == 4
    imported = case_packages.import_package(content)["case_id"]
    assert database.q("select count(*) n from import_sources where case_id=?", [imported]) == [{"n": 2}]
    assert database.q("select count(*) n from flows where case_id=?", [imported]) == [{"n": 2}]


def test_incomplete_mapping_and_entirely_unresolved_table_remain_portable(database, make_case, flow_row):
    case_id, _, flow_id = _seed_table(database, make_case, flow_row, mapping={"time_part": "时间"})
    database.x("delete from flow_sources where case_id=?", [case_id])
    database.x("delete from flows where id=?", [flow_id])
    database.x("""update import_records set disposition='unresolved',reason_code='unknown_table',
        details_json=json_object('source_row',cast(source_row as varchar),'financial_effect','undetermined')
        where case_id=? and record_no in (2,3)""", [case_id])
    database.x("update import_sources set mode='A' where case_id=?", [case_id])
    imported = case_packages.import_package(case_packages.export_package(case_id)[0])["case_id"]
    assert database.q("select count(*) n from flows where case_id=?", [imported]) == [{"n": 0}]
    source = database.q("select mode,mapping_json from import_sources where case_id=?", [imported])[0]
    assert source["mode"] == "A"
    assert json.loads(source["mapping_json"]) == {"time_part": "时间"}
    assert database.q("select count(*) n from import_records where case_id=? and disposition='unresolved'", [imported]) == [{"n": 3}]


def test_malformed_auxiliary_record_remains_unresolved_and_portable(database, make_case, flow_row):
    case_id, _, _ = _seed_table(database, make_case, flow_row)
    database.x("""update import_records set disposition='unresolved',reason_code='column_count_mismatch',
        details_json=json_merge_patch(details_json,'{"financial_effect":"undetermined"}')
        where case_id=? and record_no=7""", [case_id])
    imported = case_packages.import_package(case_packages.export_package(case_id)[0])["case_id"]
    assert database.q("select disposition,reason_code from import_records where case_id=? and record_no=7", [imported]) == [
        {"disposition": "unresolved", "reason_code": "column_count_mismatch"}]


@pytest.mark.parametrize("mutation", [
    "downgrade", "missing_journal", "profile", "invalid_mapping_key", "invalid_sheet",
    "orphan_member", "orphan_record", "unsupported_journal_source", "unsafe_path", "bad_role",
    "bad_encoding", "large_member", "row_count", "record_gap", "duplicate_position", "impossible_position", "bad_key",
    "bad_disposition", "bad_reason", "raw_details", "nested_details", "wrong_effect", "fake_zero",
    "bad_auxiliary", "unknown_issue", "missing_digest", "wrong_digest", "unresolved_digest",
    "missing_link", "extra_link", "wrong_link_disposition", "actual_amount", "actual_direction", "actual_time",
])
def test_tampered_table_journal_rejected_before_creating_case(database, make_case, flow_row, tmp_path, mutation):
    case_id, _, _ = _seed_table(database, make_case, flow_row)
    members = _members(case_packages.export_package(case_id)[0])
    if mutation == "downgrade":
        meta = json.loads(members["meta.json"])
        meta["format"] = 2
        members["meta.json"] = json.dumps(meta).encode()
    elif mutation == "missing_journal":
        members.pop("import_records.parquet")
    else:
        table, sql = {
            "profile": ("import_sources", "update entries set parser_version='funds-trace-table-v99'"),
            "invalid_mapping_key": ("import_sources", "update entries set mapping_json='{\"raw_row\":\"not a field\"}'"),
            "invalid_sheet": ("import_sources", "update entries set sheet='invented workbook'"),
            "orphan_member": ("import_members", "update entries set source_id='aabbccddeeff'"),
            "orphan_record": ("import_records", "update entries set source_id='aabbccddeeff'"),
            "unsupported_journal_source": ("import_sources", "update entries set row_kind='csv_record',parser_version='funds-trace-table-v4'"),
            "unsafe_path": ("import_members", "update entries set member_path='../table.csv' where role='table'"),
            "bad_role": ("import_members", "update entries set role='transaction' where role='table'"),
            "bad_encoding": ("import_members", "update entries set encoding='lossy-auto'"),
            "large_member": ("import_members", "update entries set size_bytes=268435457"),
            "row_count": ("import_members", "update entries set row_count=row_count+1 where role='table'"),
            "record_gap": ("import_records", "update entries set record_no=8 where record_no=7"),
            "duplicate_position": ("import_records", "update entries set source_row=3 where record_no=3"),
            "impossible_position": ("import_records", "update entries set source_row=100000,record_key='"
                                    + table_import.record_key("table.csv", hashlib.sha256(b"table.csv").hexdigest(), 100000)
                                    + "',details_json=json_merge_patch(details_json,'{\"source_row\":\"100000\"}') where record_no=4"),
            "bad_key": ("import_records", "update entries set record_key=repeat('b',64) where record_no=2"),
            "bad_disposition": ("import_records", "update entries set disposition='cancelled' where record_no=4"),
            "bad_reason": ("import_records", "update entries set reason_code='guessed' where record_no=4"),
            "raw_details": ("import_records", "update entries set details_json='{\"raw_row\":\"not permitted\"}' where record_no=4"),
            "nested_details": ("import_records", "update entries set details_json='{\"financial_effect\":{\"raw\":\"not permitted\"}}' where record_no=4"),
            "wrong_effect": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"financial_effect\":\"source_reported\"}') where record_no=4"),
            "fake_zero": ("import_records", "update entries set disposition='non_monetary',reason_code='conflicting_amount' where record_no=4"),
            "bad_auxiliary": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"source_role\":\"guessed\"}') where record_no=7"),
            "unknown_issue": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"validation_issues\":\"guessed\"}') where record_no=4"),
            "missing_digest": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"flow_sha256\":null}') where record_no=2"),
            "wrong_digest": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"flow_sha256\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\"}') where record_no=2"),
            "unresolved_digest": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"flow_sha256\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\"}') where record_no=4"),
            "missing_link": ("flow_sources", "delete from entries where source_row=2"),
            "extra_link": ("flow_sources", "insert into entries select source_id,4,flow_id,'imported' from entries limit 1"),
            "wrong_link_disposition": ("flow_sources", "update entries set disposition='duplicate' where source_row=2"),
            "actual_amount": ("flows", "update entries set amount=99.99"),
            "actual_direction": ("flows", "update entries set payer_account='OTHER-SYNTHETIC'"),
            "actual_time": ("flows", "update entries set trade_time=timestamp '2026-02-01 01:00:00'"),
        }[mutation]
        _mutate(members, table, sql, tmp_path)
    before = database.q("select id from cases order by id")
    with pytest.raises(HTTPException) as raised:
        case_packages.import_package(_archive(members))
    assert raised.value.status_code == 400
    assert database.q("select id from cases order by id") == before


def test_removed_table_records_retain_historical_digest_without_reviving_flow(database, make_case, flow_row):
    case_id, _, flow_id = _seed_table(database, make_case, flow_row)
    database.x("delete from flow_sources where case_id=?", [case_id])
    database.x("delete from flows where id=?", [flow_id])
    database.x("update import_records set disposition='removed',reason_code='flow_deleted' where case_id=? and record_no in (2,3)", [case_id])
    imported = case_packages.import_package(case_packages.export_package(case_id)[0])["case_id"]
    assert database.q("select count(*) n from flows where case_id=?", [imported]) == [{"n": 0}]
    assert database.q("select count(*) n from import_records where case_id=? and disposition='removed'", [imported]) == [{"n": 2}]


def test_table_package_transaction_failure_rolls_back_all_records(database, make_case, flow_row, monkeypatch):
    case_id, _, _ = _seed_table(database, make_case, flow_row)
    content = case_packages.export_package(case_id)[0]
    before = {table: database.q(f"select count(*) n from {table}")[0]["n"]
              for table in ("cases", "flows", "import_sources", "flow_sources", "import_members", "import_records")}
    # A table-only package has no photo publication. Inject at the end of the
    # transaction after every journal write through the final flow count query.
    original_transaction = database.transaction
    from contextlib import contextmanager

    @contextmanager
    def failing_transaction():
        with original_transaction() as connection:
            yield connection
            raise RuntimeError("synthetic rollback after complete table restore")

    monkeypatch.setattr(database, "transaction", failing_transaction)
    with pytest.raises(RuntimeError, match="synthetic rollback"):
        case_packages.import_package(content)
    assert {table: database.q(f"select count(*) n from {table}")[0]["n"] for table in before} == before
