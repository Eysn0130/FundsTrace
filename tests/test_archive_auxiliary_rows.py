"""Synthetic archive successors retain malformed auxiliary rows verbatim."""
import csv
from decimal import Decimal
import hashlib
import io
import json
import zipfile

import pytest

from app import bank_posting_pairs, case_packages, evidence_resolution, event_resolution, table_archive, table_import
from test_feedback_import_api import complete
from xls_fixtures import workbook


TX_HEADER = ["交易流水号", "交易时间", "本方账号", "对方账号", "收付标志", "交易金额", "币种"]
TX_ROWS = [
    ["SYN-A", "2026-01-01 10:00:00.123456", "000SYN-A", "000SYN-B", "出", "90071992547409.93", "CNY"],
    ["SYN-B", "2026-01-02 11:00:00.654321", "000SYN-A", "000SYN-C", "入", "2.03", "USD"],
]
FREEZE_HEADER = "银行名称 账号 冻结措施类型 冻结措施类型名称 冻结金额 冻结机关 冻结开始日期 冻结截止日期 措施序号 备注 任务流水号 查控主体类别".split()
MANIFEST_HEADER = "任务流水号 银行名称 主体类别 证账号码 账卡号 发送时间 反馈时间 反馈结果 反馈非明细结果 反馈明细结果 入库时间 入库状态 请求单号 查询结果".split()
RIGHTS_HEADER = "银行名称 账户人姓名 账号 证件类型代码 账户人证件号 权利类型 权利金额 权利序号 权利人联系方式 权利人通讯地址 权利人姓名 证件号码".split()
FREEZE_ROW = ["Synthetic bank", "000SYN-A", "SYN-TYPE", "合成措施", "999.99", "合成机关", "2026-01-01", "2026-02-01", "SYN-M", "原值,含逗号;与|及\t分隔符", "SYN-REQUEST", "合成类别"]
MANIFEST_ROW = ["SYN-TASK", "Synthetic bank", "合成类别", "", "000SYN-A", "", "2026-01-01", "合成结果", "", "", "", "", "SYN-REQUEST", "合成查询结果"]
PROFILE_PAIRS = [("funds-trace-table-archive-v57", "funds-trace-table-archive-v60"),
                 ("funds-trace-table-archive-v58", "funds-trace-table-archive-v61"),
                 ("funds-trace-table-archive-v59", "funds-trace-table-archive-v62")]


def csv_bytes(rows, delimiter=",", encoding="utf-8-sig"):
    output = io.StringIO(newline="")
    csv.writer(output, delimiter=delimiter).writerows(rows)
    return output.getvalue().encode(encoding)


def archive(members):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for path, value in members:
            target.writestr(path, value)
    return output.getvalue()


def source(branch="ordinary", *, malformed=True):
    header, rows = list(TX_HEADER), [list(row) for row in TX_ROWS]
    if branch == "evidence":
        header += ["参考号机构", "参考号渠道", "参考号业务域", "跨账簿事件参考号"]
        rows = [row + ["", "", "", ""] for row in rows]
    ledger = ("ledger.xls", workbook([("Synthetic ledger", [header, *rows])])) if branch == "xls" else ("ledger.csv", csv_bytes([header, *rows]))
    freeze = [FREEZE_ROW[:-1], [*FREEZE_ROW, ""], ["合成孤立行", ""], FREEZE_ROW] if malformed else [FREEZE_ROW]
    manifest = [[*MANIFEST_ROW, ""], MANIFEST_ROW] if malformed else [MANIFEST_ROW]
    members = [ledger, ("freeze.csv", csv_bytes([FREEZE_HEADER, *freeze])),
               ("manifest.csv", csv_bytes([MANIFEST_HEADER, *manifest]))]
    if malformed:
        members.append(("unknown-rights.csv", csv_bytes([RIGHTS_HEADER, ["合成主体", *([""] * 12)]])))
    return archive(members)


def preview(client, cid, payload):
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("synthetic.zip", payload)})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("branch,expected", [("ordinary", "funds-trace-table-archive-v60"),
                                            ("evidence", "funds-trace-table-archive-v61"),
                                            ("xls", "funds-trace-table-archive-v62")])
def test_public_auxiliary_width_dispositions_and_original_cells(client, database, make_case, branch, expected):
    cid = make_case("Synthetic auxiliary width")
    payload = source(branch)
    view = preview(client, cid, payload)
    assert view["table_summary"]["profile"] == expected
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 2, job
    base = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    facts = database.q_raw("select trade_no,trade_time,payer_account,payee_account,amount,currency from flows where case_id=? order by trade_no", [cid])
    assert [(row["trade_no"], row["trade_time"].microsecond, row["payer_account"], row["payee_account"], row["amount"], row["currency"]) for row in facts] == [
        ("SYN-A", 123456, "000SYN-A", "000SYN-B", Decimal("90071992547409.93"), "CNY"),
        ("SYN-B", 654321, "000SYN-C", "000SYN-A", Decimal("2.03"), "USD")]
    records = client.get(base + "/records").json()["items"]
    assert len(records) == 10
    auxiliary = [row for row in records if row["member_path"] in {"freeze.csv", "manifest.csv"}]
    assert [(row["member_path"], row["source_row"], row["disposition"], row["reason_code"]) for row in auxiliary] == [
        ("freeze.csv", 2, "unresolved", "column_count_mismatch"),
        ("freeze.csv", 3, "unresolved", "column_count_mismatch"),
        ("freeze.csv", 4, "unresolved", "column_count_mismatch"),
        ("freeze.csv", 5, "auxiliary", "auxiliary_table"),
        ("manifest.csv", 2, "unresolved", "column_count_mismatch"),
        ("manifest.csv", 3, "auxiliary", "auxiliary_table")]
    for row in auxiliary:
        assert row["flow_id"] is None
        if row["disposition"] == "unresolved":
            assert row["details"]["financial_effect"] == "undetermined"
            assert "resolution_evidence" not in row["details"]
    unknown = [row for row in records if row["member_path"] == "unknown-rights.csv"]
    assert len(unknown) == 2 and all(row["disposition"] == "unresolved" and row["reason_code"] == "unknown_table" for row in unknown)
    assert all("resolution_evidence" not in row["details"] and row["flow_id"] is None for row in unknown)
    for path, expected_rows in [("freeze.csv", [FREEZE_ROW[:-1], [*FREEZE_ROW, ""], ["合成孤立行", ""], FREEZE_ROW]),
                                ("manifest.csv", [[*MANIFEST_ROW, ""], MANIFEST_ROW])]:
        response = client.get(base + "/review", params={"member_path": path})
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["original_error"] is None
        assert [[cell["value"] for cell in row["original"]] for row in page["items"]] == expected_rows
    outer = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file")
    assert outer.status_code == 200 and outer.content == payload
    assert hashlib.sha256(outer.content).digest() == hashlib.sha256(payload).digest()
    coverage = client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]
    assert not coverage["financial_interpretation_complete"]


@pytest.mark.parametrize("old,new", PROFILE_PAIRS)
def test_frozen_predecessors_reject_same_malformed_auxiliary_bytes(old, new):
    with pytest.raises(table_import.TableImportError, match="CSV已识别表头与数据列数不一致"):
        table_archive.parse(io.BytesIO(source()), profile=old)


@pytest.mark.parametrize("old,new", PROFILE_PAIRS)
def test_successors_inherit_exact_parent_capabilities(old, new):
    for module in (table_import, table_archive, evidence_resolution, event_resolution, bank_posting_pairs):
        for name, profiles in vars(module).items():
            if name.endswith("_PROFILES") and isinstance(profiles, frozenset) and name != "AUXILIARY_ROWS_ARCHIVE_PROFILES":
                assert (new in profiles) == (old in profiles), (module.__name__, name)
    assert new in table_archive.SUPPORTED_PROFILES
    assert new not in table_import.SUPPORTED_PROFILES
    assert table_archive.PROFILE_PRECEDENCE.index(new) < table_archive.PROFILE_PRECEDENCE.index(old)
    assert set(table_archive.PROFILE_PRECEDENCE) == table_archive.SUPPORTED_PROFILES


@pytest.mark.parametrize("profile", [new for _, new in PROFILE_PAIRS])
@pytest.mark.parametrize("delimiter", [",", "\t", ";", "|"])
@pytest.mark.parametrize("encoding", ["utf-8-sig", "gb18030"])
def test_exact_auxiliary_header_selects_full_quoted_matrix(profile, delimiter, encoding):
    rows = [FREEZE_ROW[:-1], [*FREEZE_ROW, ""], ["合成孤立行", ""], FREEZE_ROW]
    raw = csv_bytes([FREEZE_HEADER, *rows], delimiter, encoding)
    plan = table_archive.parse(io.BytesIO(archive([("freeze.csv", raw)])), profile=profile)
    try:
        assert plan["rows"] == []
        assert [row["source_row"] for row in plan["records"]] == [2, 3, 4, 5]
        assert [row["reason_code"] for row in plan["records"]] == ["column_count_mismatch"] * 3 + ["auxiliary_table"]
        assert plan["members"][0]["sha256"] == hashlib.sha256(raw).hexdigest()
        assert plan["documents"][0]["columns"] == FREEZE_HEADER
        assert all(row["record_key"] == table_import.record_key("freeze.csv", hashlib.sha256(raw).hexdigest(), row["source_row"]) for row in plan["records"])
    finally:
        if plan.get("_details_pool") is not None:
            plan["_details_pool"].close()


@pytest.mark.parametrize("profile", [new for _, new in PROFILE_PAIRS])
@pytest.mark.parametrize("raw,message", [
    (csv_bytes([TX_HEADER, TX_ROWS[0][:-1]]), "CSV已识别表头与数据列数不一致"),
    (csv_bytes([FREEZE_HEADER]) + b'"unterminated', "CSV引号或记录结构损坏"),
    (b'date,amount,tail;date;amount\n2026-01-01,1.23,x;2026-01-02;2.34\n', "CSV分隔符存在歧义"),
    (csv_bytes([FREEZE_HEADER, FREEZE_ROW[:-1], TX_HEADER, *TX_ROWS]), "CSV已识别表头与数据列数不一致"),
])
def test_financial_damage_quotes_ambiguity_and_mixed_regions_remain_rejected(profile, raw, message):
    with pytest.raises(table_import.TableImportError, match=message):
        table_archive.parse(io.BytesIO(archive([("ledger.csv", csv_bytes([TX_HEADER, *TX_ROWS])), ("damaged.csv", raw)])), profile=profile)


@pytest.mark.parametrize("old,new", PROFILE_PAIRS)
def test_duplicate_auxiliary_header_keeps_predecessor_unknown_interpretation(old, new):
    raw = archive([("duplicate.csv", csv_bytes([[*FREEZE_HEADER, FREEZE_HEADER[-1]], [*FREEZE_ROW, ""]]))])
    before = table_archive.parse(io.BytesIO(raw), profile=old)
    after = table_archive.parse(io.BytesIO(raw), profile=new)
    try:
        assert before["rows"] == after["rows"] == []
        assert list(before["records"]) == list(after["records"])
        assert all(row["reason_code"] == "unknown_table" and "source_role" not in row["details"] for row in after["records"])
    finally:
        for plan in (before, after):
            if plan.get("_details_pool") is not None:
                plan["_details_pool"].close()


def test_auxiliary_width_allowance_keeps_column_limit(monkeypatch):
    monkeypatch.setattr(table_import, "MAX_COLUMNS", len(FREEZE_HEADER))
    with pytest.raises(table_import.TableImportError, match="记录数或列数超出安全处理范围"):
        table_archive.parse(io.BytesIO(source()), profile="funds-trace-table-archive-v60")


@pytest.mark.parametrize("branch,old", [("ordinary", "funds-trace-table-archive-v57"),
                                       ("evidence", "funds-trace-table-archive-v58"),
                                       ("xls", "funds-trace-table-archive-v59")])
def test_ready_predecessor_session_keeps_frozen_repreview_commit_and_receipt(client, database, make_case, monkeypatch, branch, old):
    from app.services import imports
    cid = make_case("Synthetic frozen auxiliary predecessor")
    original = imports._parse

    def historical(*args, **options):
        options["table_profile"] = old
        return original(*args, **options)

    with monkeypatch.context() as context:
        context.setattr(imports, "_parse", historical)
        view = preview(client, cid, source(branch, malformed=False))
    assert view["table_summary"]["profile"] == old
    assert "columns" not in next(item for item in view["table_summary"]["documents"] if item["member_path"] == "freeze.csv")
    request = json.loads(database.q_raw("select request_json from import_sessions where import_id=?", [view["import_id"]])[0]["request_json"])
    assert request["_table_profile"] == old
    base = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    refreshed = client.post(base + "/preview", json={"mode": view["mode"], "mapping": view["mapping"]})
    assert refreshed.status_code == 200 and refreshed.json()["table_summary"] == view["table_summary"]
    job = complete(client, cid, refreshed.json())
    assert job["stage"] == "done" and job["result"]["table_summary"]["profile"] == old
    before = client.get(base).json()
    assert complete(client, cid, refreshed.json()) == before
    assert database.q_raw("select parser_version from import_sources where case_id=?", [cid]) == [{"parser_version": old}]


def test_public_financial_damage_is_atomic(client, database, make_case):
    cid = make_case("Synthetic financial width rejection")
    raw = archive([("freeze.csv", csv_bytes([FREEZE_HEADER, FREEZE_ROW[:-1]])),
                   ("ledger.csv", csv_bytes([TX_HEADER, TX_ROWS[0][:-1]]))])
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("synthetic.zip", raw)})
    assert response.status_code == 400 and "CSV已识别表头与数据列数不一致" in response.text
    for table in ("flows", "batches", "import_sources", "import_members", "import_records", "import_sessions"):
        assert database.q_raw(f"select count(*) n from {table} where case_id=?", [cid]) == [{"n": 0}]


def test_auxiliary_unresolved_rows_reopen_package_repeat_withdraw_and_rollback(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic auxiliary journal lifetime")
    raw = source("evidence")
    view = preview(client, cid, raw)
    original = database.insert_dataframe

    def fail_after_records(table, frame, columns):
        result = original(table, frame, columns)
        if table == "import_records":
            raise RuntimeError("Synthetic auxiliary journal rollback")
        return result

    with monkeypatch.context() as failure:
        failure.setattr(database, "insert_dataframe", fail_after_records)
        assert complete(client, cid, view)["stage"] == "error"
    for table in ("flows", "batches", "import_sources", "flow_sources", "import_members", "import_records", "evidence_sources"):
        assert database.q_raw(f"select count(*) n from {table} where case_id=?", [cid]) == [{"n": 0}]
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    records = database.q_raw("select member_path,source_row,disposition,reason_code,details_json from import_records where case_id=? order by record_no", [cid])
    facts = database.q_raw("select trade_no,amount,trade_time from flows where case_id=? order by trade_no", [cid])
    with database._lock:
        database._con.close()
        database._con = None
    assert database.q_raw("select trade_no,amount,trade_time from flows where case_id=? order by trade_no", [cid]) == facts
    repeated = complete(client, cid, preview(client, cid, raw))
    assert repeated["stage"] == "done" and (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 2)
    assert database.q_raw("select member_path,source_row,disposition,reason_code,details_json from import_records where case_id=? and source_id=? order by record_no", [cid, view["import_id"]]) == records
    journal = database.q_raw("select member_path,source_row,disposition,reason_code,details_json from import_records where case_id=? order by all", [cid])
    package = case_packages.export_package(cid)[0]
    restored = case_packages.import_package(package)["case_id"]
    assert database.q_raw("select trade_no,amount,trade_time from flows where case_id=? order by trade_no", [restored]) == facts
    assert database.q_raw("select member_path,source_row,disposition,reason_code,details_json from import_records where case_id=? order by all", [restored]) == journal
    assert client.delete(f"/api/batches/{job['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert database.q_raw("select trade_no,amount,trade_time from flows where case_id=? order by trade_no", [cid]) == facts
    assert client.delete(f"/api/batches/{repeated['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid]) == [{"n": 0}]
