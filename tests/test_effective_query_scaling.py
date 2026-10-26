"""Sparse cancellation lookups must not multiply a whole uncommitted ledger."""
import datetime as dt
import hashlib
import io
import json

import pytest

from app import table_archive, table_cancellations, table_import
from test_table_cancellations import source, transaction


@pytest.mark.parametrize("ordered", [False, True])
def test_sparse_proofs_bound_intermediate_joins_before_commit(database, make_case, ordered):
    cid = make_case("Synthetic sparse cancellation query")
    size, pairs, stride = 512, 8, 64
    rows = []
    for position in range(1, size + 1):
        group, offset = divmod(position - 1, stride)
        amount, balance = ("10.00", "90.00") if offset == 1 else ("-10.00", "100.00") if offset == 2 else ("1.00", "100.00")
        rows.append(transaction(**{
            "交易时间": str(dt.datetime(2026, 1, 1) + dt.timedelta(minutes=position)),
            "交易流水号": f"SYN-PAIR-{group}" if offset in (1, 2) else f"SYN-NORMAL-{position}",
            "交易金额": amount, "交易余额": balance, "摘要说明": "冲正" if offset == 2 else "",
        }))
    payload, package = source(rows, path="table.csv")
    old = table_import.parse(io.BytesIO(payload), suffix=".csv", profile="funds-trace-table-v17")
    proof = table_archive.parse(io.BytesIO(package), profile=table_archive.PROFILE)
    assert len(table_cancellations.validate_evidence(proof["records"])) == pairs
    assert len(old["rows"]) == size - pairs and len(proof["rows"]) == size - 2 * pairs
    digest = hashlib.sha256(payload).hexdigest()
    fields = [*table_import.FLOW_FIELDS, "self_account", "self_name"]
    # Deliberately query before commit: the optimizer cannot assume that stored
    # table cardinalities describe rows just appended to this transaction.
    transaction_scope = database.import_transaction if ordered else database.transaction
    with transaction_scope() as connection:
        for sid, plan, filename in (("synthetic-old", old, "synthetic.csv"),
                                    ("synthetic-proof", proof, "synthetic.zip")):
            original_bytes = payload if sid == "synthetic-old" else package
            connection.execute("""insert into import_sources
                (source_id,case_id,filename,sha256,size_bytes,mode,mapping_json,sheet,parser_version,row_kind)
                values (?,?,?,?,?,'B','{}','',?,'table_record')""",
                               [sid, cid, filename, hashlib.sha256(original_bytes).hexdigest(), len(original_bytes), plan["profile"]])
            connection.execute("insert into import_members values (?,?,'table.csv','table',?,?,'utf-8-sig',?)",
                               [cid, sid, digest, len(payload), size])
        connection.executemany(f"insert into flows(id,case_id,batch_id,{','.join(fields)}) values (?,?,1,{','.join('?' for _ in fields)})",
                               [[row["_source_row"], cid, *[row[field] for field in fields]] for row in old["rows"]])
        for sid, plan in (("synthetic-old", old), ("synthetic-proof", proof)):
            admitted = "imported" if sid == "synthetic-old" else "duplicate"
            connection.executemany("insert into import_records values (?,?,?,?,?,?,?,?,?)", [
                [cid, sid, record["record_no"], record["member_path"], record["source_row"], record["record_key"],
                 admitted if record["disposition"] == "accepted" else record["disposition"],
                 record["reason_code"], json.dumps(record["details"])] for record in plan["records"]])
            connection.executemany("insert into flow_sources values (?,?,?,?,?)",
                                   [[cid, sid, row["_source_row"], row["_source_row"], admitted] for row in plan["rows"]])
        for view in ("source_effective_flows", "effective_flows"):
            query = f"select count(*) from {view} where case_id=?"
            assert connection.execute(query, [cid]).fetchone()[0] == size - 2 * pairs
            execution = json.loads(connection.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + query, [cid]).fetchone()[1])
            def join_sizes(node):
                if node.get("operator_type") == "HASH_JOIN":
                    yield node["operator_cardinality"]
                for child in node.get("children", []):
                    yield from join_sizes(child)
            # There are two source journals and at most two links per flow.
            # No lookup needs a case-wide or member-wide cross product.
            assert max(join_sizes(execution), default=0) <= 2 * size
            if ordered:
                def scans_details(node):
                    projections = node.get("extra_info", {}).get("Projections", [])
                    return (node.get("operator_type") == "TABLE_SCAN" and "details_json" in projections
                            or any(scans_details(child) for child in node.get("children", [])))
                def check_builds(node):
                    if node.get("operator_type") == "HASH_JOIN":
                        build = node["children"][1]
                        if scans_details(build):
                            assert build["operator_cardinality"] <= 2 * pairs
                    for child in node.get("children", []):
                        check_builds(child)
                check_builds(execution)
