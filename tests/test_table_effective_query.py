"""Public effective-money reads keep JSON checks out of table hash-join keys."""
import json
from decimal import Decimal

from app import table_archive
from test_archive_profile_upgrade import frozen_preview, source, transaction
from test_automatic_table_import import preview
from test_feedback_import_api import complete


def _source():
    rows = [
        transaction("SYN-WITNESS", "SYN-WITNESS", "1.00", **{"交易余额": "100.00"}),
        transaction("SYN-PROTECTED", "SYN-PROTECTED", "10.00", **{
            "交易时间": "2026-01-01 10:01:00", "交易余额": "90.00"}),
        transaction("SYN-PROTECTED", "SYN-PROTECTED", "-10.00", **{
            "交易时间": "2026-01-01 10:02:00", "交易余额": "100.00", "摘要说明": "冲正"}),
    ]
    rows.extend(transaction(f"SYN-ACTIVE-{index}", f"SYN-ACTIVE-{index}", "2.00", **{
        "交易时间": f"2026-01-01 10:{index:02d}:00", "交易余额": "100.00"}) for index in range(3, 21))
    rows.extend([
        transaction("SYN-SECOND-WITNESS", "SYN-SECOND-WITNESS", "1.00", **{
            "交易时间": "2026-01-01 11:00:00", "交易余额": "200.00", "交易账号": "SYN-SECOND"}),
        transaction("SYN-EXCLUDED", "SYN-EXCLUDED", "7.00", **{
            "交易时间": "2026-01-01 11:01:00", "交易余额": "193.00", "交易账号": "SYN-SECOND"}),
        transaction("SYN-EXCLUDED", "SYN-EXCLUDED", "-7.00", **{
            "交易时间": "2026-01-01 11:02:00", "交易余额": "200.00", "交易账号": "SYN-SECOND", "摘要说明": "冲正"}),
    ])
    return rows, source(rows)[1]


def _nodes(plan):
    for node in plan:
        yield node
        yield from _nodes(node.get("children", []))


def test_repeated_table_proofs_keep_public_totals_and_bound_json_join_work(client, database, make_case, monkeypatch):
    cid, other = make_case("Synthetic table query"), make_case("Synthetic table query other")
    rows, content = _source()
    for _ in range(3):
        job = complete(client, cid, frozen_preview(client, cid, content, monkeypatch, table_archive.REFERENCE_PROFILE))
        assert job["stage"] == "done", job
    assert complete(client, other, frozen_preview(client, other, content, monkeypatch,
                                                table_archive.REFERENCE_PROFILE))["stage"] == "done"
    # Preserve a known legacy source link without a disposition journal. The
    # linked flow still represents an active source occurrence after pairing.
    legacy = complete(client, cid, preview(client, cid, source([rows[1]])[0]))
    assert legacy["result"]["ok"] == 0 and legacy["result"]["rows_dup"] == 1
    legacy_source = legacy["result"]["source_id"]
    database.x("delete from import_records where case_id=? and source_id=?", [cid, legacy_source])
    database.x("delete from import_members where case_id=? and source_id=?", [cid, legacy_source])
    database.x("update import_sources set row_kind='csv_record',parser_version='funds-trace-table-v4' where source_id=?",
               [legacy_source])
    for _ in range(3):
        job = complete(client, cid, preview(client, cid, content, "synthetic.zip"))
        assert job["stage"] == "done" and job["result"]["ok"] == 0, job
        assert job["result"]["table_summary"]["cancelled"] == 4
        assert job["result"]["table_summary"]["excluded_flows"] == 1
    stats = client.get(f"/api/v1/cases/{cid}/stats").json()
    assert stats["flows"] == 21 and stats["amount_totals"] == [{"currency": "CNY", "amount": "48.00"}]
    assert client.get(f"/api/v1/cases/{other}/stats").json()["flows"] == 22
    public_rows = client.get(f"/api/v1/cases/{cid}/flows", params={"page_size": 100}).json()
    assert public_rows["total"] == 21
    numbers = {row["trade_no"] for row in public_rows["items"]}
    assert "SYN-PROTECTED" in numbers and "SYN-EXCLUDED" not in numbers
    assert database.q_raw("select count(*) n,sum(amount) amount from flows where case_id=?", [cid]) == [
        {"n": 22, "amount": Decimal("55.00")}]

    plan = json.loads(database.con().execute(
        "EXPLAIN (FORMAT JSON) select count(*) from effective_flows where case_id=?", [cid]).fetchone()[1])
    # JSON equality in a hash key decodes complete journal payloads during the
    # join build, before physical source matching can narrow the candidates.
    # Check that specific work placement, not a golden plan or operator count.
    expensive_keys = [node["extra_info"].get("Conditions") for node in _nodes(plan)
                      if node["name"] == "HASH_JOIN" and "json_extract" in json.dumps(
                          node.get("extra_info", {}).get("Conditions", "")).lower()]
    assert expensive_keys == []
