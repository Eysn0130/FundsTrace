"""A member report never expands ledger payloads from other source scopes."""
from decimal import Decimal
import json

import pytest

from app import source_ledger, table_archive, table_import
from app.services import import_report
from test_source_ledger_commit_snapshot import evidence


SOURCE = "SYN-REPORT-SOURCE"
MEMBER = "selected.csv"


def seed(database, cid):
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,mapping_json,sheet,parser_version,row_kind)
        values (?,?,'synthetic.zip',?,0,'B','{}','',?,'table_record')""", [SOURCE, cid, "a" * 64, table_archive.AUXILIARY_ROWS_EVIDENCE_PROFILE])
    for member in (MEMBER, "other.csv"):
        database.x("insert into import_members values (?,?,?,'table',?,0,'utf-8-sig',1000)", [cid, SOURCE, member, "b" * 64])
    records = []
    for number in range(1, 1001):
        member = MEMBER if number in {1, 1000} else "other.csv"
        value = evidence(number, kind="financial")
        value["row"]["summary"] = "Synthetic unrelated wide note " * 128
        details = json.dumps({source_ledger.FIELD: json.dumps(value, ensure_ascii=False)}, ensure_ascii=False)
        records.append([cid, SOURCE, number, member, number + 1,
                        table_import.record_key(member, "b" * 64, number + 1), "imported", "transaction_valid", details])
    database.con().executemany("insert into import_records values (?,?,?,?,?,?,?,?,?)", records)
    for number in (1, 1000):
        database.x("""insert into flows(id,case_id,trade_time,payer_account,payee_account,self_account,amount,currency,balance)
            values (?,?,timestamp '2026-01-01 09:00:00','000SYN-A','000SYN-B','000SYN-A',0.01,'CNY',100.00)""", [number, cid])
        database.x("insert into flow_sources values (?,?,?,?, 'imported')", [cid, SOURCE, number, number])


@pytest.mark.parametrize("outside", ["member", "source", "case"])
def test_member_selection_expands_only_its_ledger_payload(database, make_case, outside):
    cid = make_case("Synthetic report ledger scope")
    seed(database, cid)
    if outside != "member":
        other = cid if outside == "source" else make_case("Synthetic other ledger case")
        database.x("""insert into import_sources select * replace ('SYN-OTHER-SOURCE' as source_id,? as case_id)
            from import_sources where case_id=? and source_id=?""", [other, cid, SOURCE])
        database.x("""insert into import_members select * replace ('SYN-OTHER-SOURCE' as source_id,? as case_id)
            from import_members where case_id=? and source_id=?""", [other, cid, SOURCE])
        database.x("""insert into import_records select * replace ('SYN-OTHER-SOURCE' as source_id,? as case_id)
            from import_records where case_id=? and source_id=?""", [other, cid, SOURCE])
    statement = database.q_raw("select sql from duckdb_views() where view_name='source_ledger_records'")[0]["sql"]
    database.x(statement.replace("source_ledger_records", "synthetic_report_ledger_base", 1))
    calls = []

    def payload(case_id, source_id, member_path, number, value):
        calls.append((case_id, source_id, member_path, number))
        return value

    connection = database.con()
    connection.create_function("synthetic_report_ledger_payload", payload,
                               ["INTEGER", "VARCHAR", "VARCHAR", "BIGINT", "VARCHAR"], "VARCHAR")
    database.x("""create or replace view source_ledger_records as select l.* replace (
        synthetic_report_ledger_payload(case_id,source_id,member_path,record_no,ledger_evidence) as ledger_evidence)
        from synthetic_report_ledger_base l""")
    try:
        prefix, params = import_report._selection(cid, SOURCE, MEMBER)
        assert params == [cid, SOURCE, MEMBER, cid, SOURCE, MEMBER]
        rows = database.q_raw(prefix + """select record_no,source_direction,own_number,amount,currency,fact_status
            from _report_support order by record_no""", params)
        assert rows == [{"record_no": number, "source_direction": "out", "own_number": "000SYN-A",
                         "amount": Decimal("0.01"), "currency": "CNY", "fact_status": "unresolved"} for number in (1, 1000)]
        assert calls
        assert set(calls) == {(cid, SOURCE, MEMBER, 1), (cid, SOURCE, MEMBER, 1000)}
    finally:
        database.x("create or replace view source_ledger_records as select * from synthetic_report_ledger_base")
        connection.remove_function("synthetic_report_ledger_payload")
