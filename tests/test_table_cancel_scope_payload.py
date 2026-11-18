"""History gates retain exact bags and null-extension of cancellation facts."""
from collections import Counter
from decimal import Decimal
import hashlib

from app import table_archive, table_cancellation_links as links


def query(*, controlled=False):
    prefix = links.ctes()
    if controlled:
        # Exercise the unchanged public link/fact CTEs with an independently
        # specified history boundary, including duplicate and missing support.
        marker = "a02_history_source_facts as materialized ("
        start = prefix.index(marker) + len(marker)
        end = prefix.index("), a02_", start)
        prefix = prefix[:start] + "select * from synthetic_history_source_facts\n    " + prefix[end:]
    return "with recursive " + prefix


def flow(database, cid, number, amount, summary, account="SYN-OWN", name="Synthetic owner", balance=None):
    database.x("""insert into flows(id,case_id,trade_time,amount,currency,balance,summary,self_account,self_name)
        values (?,?,timestamp '2026-01-01 09:00:00.123456',?,'CNY',?,?,?,?)""",
               [number, cid, Decimal(amount), Decimal(balance) if balance is not None else None, summary, account, name])


def test_public_empty_proof_scope_keeps_both_materialized_gate_inputs_empty(database, make_case):
    cid = make_case("Synthetic empty cancellation scope")
    database.x("""insert into flows(id,case_id,trade_time,amount,currency,summary)
        select n,?,timestamp '2026-01-01',1.01,'CNY','Synthetic wide payload ' || repeat('x',4096)
        from range(1,1001) r(n)""", [cid])
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,mapping_json,sheet,parser_version,row_kind)
        values ('SYN-SOURCE',?,'synthetic.zip',?,0,'B','{}','',?,'table_record')""", [cid, "a" * 64, table_archive.PROFILE])
    database.x("insert into import_members values (?,'SYN-SOURCE','rows.csv','table',?,0,'utf-8-sig',1000)", [cid, "b" * 64])
    database.x("""insert into import_records select ?,'SYN-SOURCE',n,'rows.csv',n+1,cast(n as varchar),
        'imported','transaction_valid','{}' from range(1,1001) r(n)""", [cid])
    database.x("insert into flow_sources select ?,'SYN-SOURCE',n,n,'imported' from range(1,1001) r(n)", [cid])
    # Measure the relations retained before each RIGHT JOIN. A UDF on the base
    # flows projection may run before an empty gate and cannot measure that.
    assert database.q_raw(query() + """select
        (select count(*) from a02_history_flow_sources) source_rows,
        (select count(*) from a02_history_flow_rows) flow_rows,
        (select count(*) from a02_history_links) history_links,
        (select count(*) from a02_history_facts) history_facts,
        (select count(*) from a02_valid) valid_pairs""") == [
        {"source_rows": 0, "flow_rows": 0, "history_links": 0, "history_facts": 0, "valid_pairs": 0}]
    assert database.q_raw("select count(*) n,sum(amount) amount from effective_flows where case_id=?", [cid]) == [
        {"n": 1000, "amount": Decimal("1010.00")}]


def test_history_fact_bag_keeps_missing_links_nulls_repeats_and_case_source_keys(database, make_case):
    cid, other = make_case("Synthetic gate history"), make_case("Synthetic foreign gate history")
    database.x("""create table synthetic_history_source_facts(case_id integer,source_id varchar,record_no bigint,
        disposition varchar,validation_conflict boolean,flow_sha varchar,history_ledger json)""")
    rows = [(cid, "SYN-A", 1, "imported", False, None, None),
            (cid, "SYN-A", 2, "duplicate", False, None, None),
            (cid, "SYN-A", 3, "imported", False, None, None),
            (cid, "SYN-A", 4, "imported", False, None, None),
            (cid, "SYN-A", 5, "imported", False, None, None),
            (cid, "SYN-B", 1, "imported", False, None, None),
            (other, "SYN-A", 1, "imported", False, None, None)]
    rows.append(rows[0])
    database.con().executemany("insert into synthetic_history_source_facts values (?,?,?,?,?,?,?)", rows)
    flow(database, cid, 17, "90071992547409.91", "Synthetic selected wide payload", balance="12.34")
    flow(database, cid, 18, "-0.01", "Synthetic selected negative payload", account="SYN-NEGATIVE", name="")
    flow(database, other, 19, "3.01", "Synthetic selected foreign payload", account="SYN-FOREIGN", name="Foreign owner")
    flow(database, cid, 9999, "999.99", "Synthetic unrelated payload")
    database.x("insert into source_ledger_index(case_id,flow_id) values (?,18)", [cid])
    supports = [(cid, "SYN-A", 1, 17, "imported"), (cid, "SYN-A", 2, 17, "duplicate"),
                (cid, "SYN-A", 4, 999, "imported"), (cid, "SYN-A", 5, 19, "imported"),
                (cid, "SYN-B", 1, 18, "imported"), (other, "SYN-A", 1, 19, "imported"),
                (cid, "SYN-UNRELATED", 1, 9999, "imported")]
    database.con().executemany("insert into flow_sources values (?,?,?,?,?)", supports)
    selected_sources = database.q_raw(query(controlled=True) + """select case_id,source_id,source_row,flow_id,disposition
        from a02_history_flow_sources""")
    assert Counter(tuple(row.values()) for row in selected_sources) == Counter([
        (cid, "SYN-A", 1, 17, "imported"), (cid, "SYN-A", 2, 17, "duplicate"),
        (cid, "SYN-A", 4, 999, "imported"), (cid, "SYN-A", 5, 19, "imported"),
        (cid, "SYN-B", 1, 18, "imported"), (other, "SYN-A", 1, 19, "imported")])
    selected_flows = database.q_raw(query(controlled=True) + """select case_id,id,amount,currency,balance,summary,self_account,self_name
        from a02_history_flow_rows""")
    assert Counter(tuple(row.values()) for row in selected_flows) == Counter([
        (cid, 17, Decimal("90071992547409.91"), "CNY", Decimal("12.34"),
         "Synthetic selected wide payload", "SYN-OWN", "Synthetic owner"),
        (cid, 18, Decimal("-0.01"), "CNY", None, "Synthetic selected negative payload", "SYN-NEGATIVE", ""),
        (other, 19, Decimal("3.01"), "CNY", None, "Synthetic selected foreign payload", "SYN-FOREIGN", "Foreign owner")])
    actual = database.q_raw(query(controlled=True) + """select case_id,source_id,record_no,disposition,linked_id,actual_id,
        source_ledger_observation,ledger_amount,ledger_balance,ledger_summary,account_sha,name_sha from a02_history_facts""")
    sha = lambda value: hashlib.sha256(value.encode()).hexdigest() if value else ""
    matched = (17, 17, False, Decimal("90071992547409.91"), Decimal("12.34"), "Synthetic selected wide payload", sha("SYN-OWN"), sha("Synthetic owner"))
    expected = [(cid, "SYN-A", 1, "imported", *matched),
                (cid, "SYN-A", 1, "imported", *matched),
                (cid, "SYN-A", 2, "duplicate", *matched),
                (cid, "SYN-A", 3, "imported", None, None, False, None, None, None, "", ""),
                (cid, "SYN-A", 4, "imported", 999, None, False, None, None, None, "", ""),
                (cid, "SYN-A", 5, "imported", 19, None, False, None, None, None, "", ""),
                (cid, "SYN-B", 1, "imported", 18, 18, True, Decimal("-0.01"), None, "Synthetic selected negative payload", sha("SYN-NEGATIVE"), ""),
                (other, "SYN-A", 1, "imported", 19, 19, False, Decimal("3.01"), None, "Synthetic selected foreign payload", sha("SYN-FOREIGN"), sha("Foreign owner"))]
    assert Counter(tuple(row.values()) for row in actual) == Counter(expected)
