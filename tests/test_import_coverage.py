"""Synthetic semantic coverage and the public outputs that disclose its limits."""
import io
import json
import re
import uuid

import openpyxl

from app.import_coverage import import_coverage
from app.services import cases


def assessment(database, case_id, disposition, *, version=1, day=1, row=2,
               member="synthetic/transactions.csv", member_hash="a" * 64, reason="synthetic"):
    source_id = uuid.uuid4().hex
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,
                   mapping_json,sheet,parser_version,row_kind,imported_at)
                   values (?,?,'synthetic.zip',?,100,'B','{}','',?,'archive_record',?)""",
               [source_id, case_id, "b" * 64, f"funds-trace-bank-feedback-v{version}", f"2026-01-{day:02d} 00:00:00"])
    database.x("""insert into import_members(case_id,source_id,member_path,role,sha256,size_bytes,encoding,row_count)
                   values (?,?,?,'transaction',?,100,'utf-8-sig',10)""",
               [case_id, source_id, member, member_hash])
    database.x("""insert into import_records(case_id,source_id,record_no,member_path,source_row,record_key,
                   disposition,reason_code,details_json) values (?,?,1,?,?,?,?,?,'{}')""",
               [case_id, source_id, member, row, uuid.uuid4().hex * 2, disposition, reason])
    return source_id


def expected(unresolved=0, non_monetary=0):
    return {"scope": "source_reported_flows", "unresolved_records": unresolved,
            "non_monetary_records": non_monetary, "cancelled_records": 0, "excluded_flows": 0,
            "financial_interpretation_complete": unresolved == 0}


def test_coverage_is_case_scoped_and_counts_physical_occurrences_once(database, make_case, monkeypatch):
    cid, other = make_case("Coverage"), make_case("Other coverage")
    assert import_coverage(cid) == expected()
    assessment(database, cid, "review")
    assessment(database, cid, "review", day=2)
    assessment(database, cid, "unresolved", version=2, row=3)
    assessment(database, cid, "review", member_hash="c" * 64)
    assessment(database, other, "review", row=4)
    original_query, queries = database.q_raw, []

    def count_query(sql, params=None):
        queries.append(sql)
        return original_query(sql, params)

    monkeypatch.setattr(database, "q_raw", count_query)
    assert import_coverage(cid) == expected(3)
    # One source-pair metadata query plus one set-based coverage query. The
    # resolver must never issue a query for every physical transaction row.
    assert len(queries) == 2


def test_supported_version_precedes_time_and_latest_same_version_includes_removal(database, make_case):
    cid = make_case("Versioned coverage")
    for row in (2, 3, 4):
        assessment(database, cid, "review", version=1, day=3, row=row)
    assessment(database, cid, "imported", version=2, day=1, row=2)
    assessment(database, cid, "non_monetary", version=2, day=1, row=3)
    assessment(database, cid, "unresolved", version=2, day=1, row=4)
    assessment(database, cid, "removed", version=2, day=2, row=4)
    assessment(database, cid, "non_monetary", version=2, day=1, row=5)
    assessment(database, cid, "unresolved", version=2, day=2, row=5)
    assessment(database, cid, "review", version=1, day=1, row=6)
    assessment(database, cid, "removed", version=1, day=2, row=6)
    assert import_coverage(cid) == expected(1, 1)
    assert database.q("select count(*) n from import_records where disposition='review'") == [{"n": 4}]


def test_unknown_rule_version_is_a_gap_even_for_imported_records(database, make_case):
    cid = make_case("Future rule coverage")
    assessment(database, cid, "imported", version=99)
    assert import_coverage(cid) == expected(1)


def test_unknown_rules_cannot_be_hidden_by_supported_evaluations_or_removal(database, make_case):
    cid = make_case("Downgraded rule coverage")
    assessment(database, cid, "imported", version=99, day=1)
    assessment(database, cid, "imported", version=2, day=2)
    assessment(database, cid, "removed", version=99, day=1, row=3)
    assessment(database, cid, "removed", version=2, day=2, row=3)
    assert import_coverage(cid) == expected(2)


def test_identity_conflict_is_not_resolved_by_reimporting_original_identity(database, make_case):
    cid = make_case("Conflicting identity coverage")
    assessment(database, cid, "imported", version=2, day=1)
    assessment(database, cid, "unresolved", version=2, day=2, reason="own_identity_conflict")
    assert import_coverage(cid) == expected(1)
    assessment(database, cid, "duplicate", version=2, day=3)
    assert import_coverage(cid) == expected(1)
    assessment(database, cid, "non_monetary", version=2, day=4)
    assert import_coverage(cid) == expected(1)
    assessment(database, cid, "removed", version=2, day=5)
    assert import_coverage(cid) == expected(1)


def test_stats_analysis_and_html_disclose_the_same_coverage(client, database, make_case):
    cid = make_case("Coverage outputs")
    assessment(database, cid, "unresolved", version=2)
    assessment(database, cid, "non_monetary", version=2, row=3)
    coverage = expected(1, 1)
    assert cases.stats(str(cid))["import_coverage"] == coverage
    assert client.get("/api/stats", params={"case_id": cid}).json()["import_coverage"] == coverage
    analysis = client.post("/api/analysis/run", json={"case_id": cid, "params": {"algorithms": ["cycles"]}})
    assert analysis.status_code == 200
    assert analysis.json()["source_snapshot"]["import_coverage"] == coverage
    report = client.post("/api/analysis/report", json={"case_id": cid, "params": {"algorithms": ["cycles"]}})
    assert report.status_code == 200
    assert "资金效果未确定记录 1 条" in report.text
    assert "未计入流水统计" in report.text and "不代表结算净额" in report.text
    assert "不表示银行提供的数据齐全" in report.text
    snapshot = json.loads(re.search(r'id="funds-trace-report-data">(.*?)</script>', report.text, re.S).group(1))
    assert snapshot["results"]["source_snapshot"]["import_coverage"] == coverage


def test_tracked_excel_keeps_exact_money_and_discloses_case_coverage(client, database, make_case):
    cid = make_case("Coverage workbook")
    assessment(database, cid, "review")
    flow_id = database.q("""insert into flows(case_id,trade_time,payer_account,payee_account,amount,currency)
                             values (?,'2026-01-01 10:00:00','SYN-A','SYN-B',12.34,'CNY') returning id""", [cid])[0]["id"]
    database.x("insert into tracked_flows(case_id,flow_id) values (?,?)", [cid, flow_id])
    response = client.get("/api/export/tracked.xlsx", params={"case_id": cid})
    assert response.status_code == 200
    workbook = openpyxl.load_workbook(io.BytesIO(response.content), data_only=False)
    assert "统计口径" in workbook.sheetnames
    notes = dict(list(workbook["统计口径"].values)[1:])
    assert notes["资金效果未确定记录数"] == 1
    assert notes["已导入来源资金语义是否完整"] == "否"
    assert "不代表结算净额" in notes["流水口径"]
    values = list(workbook["追踪流水"].values)
    assert dict(zip(values[0], values[1]))["交易金额"] == "12.34"
