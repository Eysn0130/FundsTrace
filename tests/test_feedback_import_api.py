"""Synthetic public-flow checks for ZIP facts, review and durable provenance."""
import csv
import datetime as dt
import hashlib
import io
import json
import time
import zipfile
from decimal import Decimal

import pytest

from app import db, case_packages
from app.feedback_import import COLUMNS, SCHEMAS
from app.services import imports


def transaction(**changes):
    row = {column: "" for column in COLUMNS}
    row.update({"交易卡号": "SYN-CARD", "交易账号": "SYN-A", "交易时间": "2026-01-01 10:00:00",
                "交易金额": "10.25", "交易余额": "100.00", "收付标志": "出", "交易对手账卡号": "SYN-B",
                "对手户名": "Synthetic counterparty", "对手开户银行": "Synthetic bank", "摘要说明": "Synthetic payment",
                "交易币种": "CNY", "交易是否成功": "1", "交易流水号": "SYN-T1", "备注": "Synthetic remark",
                "查询反馈结果原因": "成功"})
    row.update(changes)
    return row


def archive(rows, *, account=True, owner_name="Synthetic owner", own_bank="Synthetic own bank", account_remark=""):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as package:
        def write(path, columns, records):
            text = io.StringIO(newline="")
            writer = csv.writer(text, quoting=csv.QUOTE_ALL)
            writer.writerow([c + "\t" for c in columns])
            for record in records:
                writer.writerow([record.get(c, "") + "\t" for c in columns])
            package.writestr(path, text.getvalue().encode("gb18030"))
        write("synthetic/transactions.csv", COLUMNS, rows)
        if account:
            write("synthetic/accounts.csv", SCHEMAS["account"], [
                {"交易账号": "SYN-A", "交易卡号": "SYN-CARD", "账户开户名称": owner_name,
                 "账号开户银行": own_bank, "备注": account_remark}])
    return result.getvalue()


def preview(client, cid, content):
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("synthetic.zip", content)})
    assert response.status_code == 200, response.text
    return response.json()


def complete(client, cid, view):
    base = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    response = client.post(base + "/commit", json={"mode": view["mode"], "mapping": view["mapping"], "mark_accounts": []})
    assert response.status_code == 200, response.text
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(base)
        assert response.status_code == 200, response.text
        result = response.json()
        if result["stage"] in ("done", "error"):
            return result
        time.sleep(.01)
    raise AssertionError("Synthetic ZIP task timed out")


def mixed_rows():
    first = transaction()
    return [first,
            transaction(**{"交易流水号": "SYN-T2", "收付标志": "进", "交易币种": "USD", "交易金额": "20.50", "交易对手账卡号": "", "对手户名": ""}),
            transaction(**{"交易流水号": "SYN-T3", "交易币种": "EUR", "交易金额": "2.03", "交易对手账卡号": "", "对手户名": "Synthetic name without account"}),
            transaction(**{"交易流水号": "SYN-T4", "交易金额": "-10.25", "摘要说明": "冲正"}),
            transaction(**{"交易流水号": "SYN-T5", "交易金额": "0.00"}),
            transaction(**{"交易流水号": "SYN-T6", "收付标志": ""}),
            {"查询反馈结果原因": "无交易记录"}, dict(first)]


def test_zip_public_import_conservation_exact_money_reimport_and_sources(client, database, make_case):
    cid, other = make_case("ZIP public"), make_case("ZIP other")
    content = archive(mixed_rows())
    view = preview(client, cid, content)
    assert (view["stats"]["total"], view["stats"]["ok"], view["stats"]["skipped"]) == (8, 4, 4)
    assert view["feedback"]["record_total"] == 9
    assert view["feedback"]["currency_totals"] == {"CNY": "20.50", "USD": "20.50", "EUR": "2.03"}
    assert view["preview"][0]["source_member"] == "synthetic/transactions.csv"
    assert view["preview"][0]["source_row"] == 2
    assert view["preview"][0]["payer_name"] == "Synthetic owner"
    assert "Synthetic payment" in view["preview"][0]["summary"] and "Synthetic remark" in view["preview"][0]["summary"]
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (4, 0)
    source = view["import_id"]
    records = client.get(f"/api/v1/cases/{cid}/imports/{source}/records").json()
    assert records["total"] == 9
    assert [r["disposition"] for r in records["items"]] == ["imported"] * 3 + ["unresolved", "non_monetary", "unresolved", "non_transaction", "imported", "auxiliary"]
    assert records["items"][3]["details"]["signed_amount"] == "-10.25"
    report = client.get(f"/api/v1/cases/{cid}/sources/{source}/report")
    assert report.status_code == 200 and len(report.json()["items"]) == 9
    original = client.get(f"/api/v1/cases/{cid}/sources/{source}/file")
    assert original.content == content
    assert report.json()["sha256"] == hashlib.sha256(content).hexdigest()
    assert client.get(f"/api/v1/cases/{other}/sources/{source}/report").status_code == 404
    assert client.get(f"/api/v1/cases/{other}/imports/{source}/records").status_code == 404
    rows = database.q_raw("select * from flows where case_id=? order by id", [cid])
    assert len(rows) == 4
    assert rows[1]["payer_account"] == "" and rows[1]["payee_account"] == "SYN-A"
    assert rows[2]["payee_account"] == "" and rows[2]["payee_name"] == "Synthetic name without account"
    assert rows[0]["payer_bank"] == "Synthetic own bank" and rows[0]["payee_bank"] == "Synthetic bank"
    totals = database.con().execute("select currency,sum(amount) from flows where case_id=? group by currency", [cid]).fetchall()
    assert dict(totals) == {"CNY": Decimal("20.50"), "USD": Decimal("20.50"), "EUR": Decimal("2.03")}
    source_view = client.get(f"/api/v1/cases/{cid}/flows/{rows[0]['id']}/sources").json()["items"][0]
    assert (source_view["member_path"], source_view["member_row"], source_view["row_kind"]) == ("synthetic/transactions.csv", 2, "archive_record")
    again = complete(client, cid, preview(client, cid, content))
    assert (again["result"]["ok"], again["result"]["rows_dup"]) == (0, 4)
    assert len(database.q("select id from flows where case_id=?", [cid])) == 4
    assert complete(client, other, preview(client, other, content))["result"]["ok"] == 4


def test_zip_missing_counterparty_preserves_facts_without_graph_connections(client, database, make_case):
    cid = make_case("Missing endpoints")
    job = complete(client, cid, preview(client, cid, archive(mixed_rows())))
    assert job["stage"] == "done"
    rows = database.q("select id,payer_account,payee_account from flows where case_id=? order by id", [cid])
    ids = [r["id"] for r in rows]
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": ids}).status_code == 422
    assert database.q("select count(*) n from tracked_flows where case_id=?", [cid]) == [{"n": 0}]
    assert client.get(f"/api/context/{ids[1]}", params={"case_id": cid, "side": "payer"}).status_code == 422
    assert client.get(f"/api/context/{ids[1]}", params={"case_id": cid, "side": "payee"}).status_code == 200
    profile = client.get("/api/accounts/SYN-A/profile", params={"case_id": cid}).json()
    assert profile["cnt"] == 4 and all(r["acct"] for r in profile["top_counterparties"])
    # Old snapshots/collections can contain incomplete rows; graph still guards.
    for fid in ids:
        database.x("insert into tracked_flows(case_id,flow_id) values (?,?)", [cid, fid])
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert {n["account"] for n in graph["nodes"]} == {"SYN-A", "SYN-B"}
    assert {(e["source"], e["target"]) for e in graph["edges"]} == {("SYN-A", "SYN-B")}


def test_all_unresolved_completes_automatically_and_protects_original(client, database, make_case):
    cid = make_case("Review only")
    content = archive([transaction(**{"交易金额": "-10.25", "摘要说明": "冲正"})], account=False)
    view = preview(client, cid, content)
    assert view["stats"]["ok"] == 0 and view["feedback"]["unresolved"] == 1
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 0
    assert "自动处理完成" in job["message"] and (job["done"], job["total"]) == (1, 1)
    assert database.q("select count(*) n from flows") == [{"n": 0}]
    database.x("update import_sessions set expires_at=?", [dt.datetime(2020, 1, 1)])
    result = imports.cleanup_uploads(cid, dry_run=False)
    assert result["protected"] >= 1 and result["deleted"] == 0
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == content


def test_journal_failure_rolls_back_whole_zip_and_receipt(client, database, make_case, monkeypatch):
    cid = make_case("Atomic ZIP")
    view = preview(client, cid, archive(mixed_rows()))
    original = database.insert_dataframe
    def fail_journal(table, frame, columns):
        if table == "import_records":
            raise RuntimeError("Synthetic journal failure")
        return original(table, frame, columns)
    monkeypatch.setattr(database, "insert_dataframe", fail_journal)
    assert complete(client, cid, view)["stage"] == "error"
    for table in ("flows", "batches", "import_sources", "flow_sources", "import_records", "import_members"):
        assert database.q(f"select count(*) n from {table}") == [{"n": 0}]
    assert database.q_raw("select result_json from import_sessions")[0]["result_json"] is None


def test_fixed_zip_plan_source_change_and_explicit_batch_removal(client, database, make_case):
    cid = make_case("Fixed ZIP")
    content = archive(mixed_rows())
    view = preview(client, cid, content)
    base = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    wrong = {**view["mapping"], "summary": "备注"}
    assert client.post(base + "/preview", json={"mode": "B", "mapping": wrong}).status_code == 422
    assert client.post(base + "/commit", json={"mode": "B", "mapping": wrong}).status_code == 422
    job = complete(client, cid, view)
    assert job["stage"] == "done"
    assert client.delete(f"/api/batches/{job['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    states = database.q("select disposition,count(*) n from import_records group by disposition")
    assert {r["disposition"]: r["n"] for r in states} == {"removed": 4, "unresolved": 2, "non_monetary": 1, "non_transaction": 1, "auxiliary": 1}
    assert complete(client, cid, preview(client, cid, content))["result"]["ok"] == 4
    altered = preview(client, cid, content)
    (database.DATA_DIR / "uploads" / f"{altered['import_id']}.zip").write_bytes(b"changed")
    response = client.post(f"/api/v1/cases/{cid}/imports/{altered['import_id']}/commit", json={"mode": "B", "mapping": altered["mapping"]})
    assert response.status_code == 409 and response.json()["error"]["code"] == "source_changed"


def test_changed_auxiliary_identity_never_counts_the_same_physical_money_twice(client, database, make_case):
    cid = make_case("Physical occurrence financial guard")
    first = complete(client, cid, preview(client, cid, archive([transaction()])))
    assert first["result"]["ok"] == 1
    before = database.q_raw("select * from flows where case_id=?", [cid])
    changed = complete(client, cid, preview(client, cid, archive(
        [transaction()], owner_name="Synthetic revised owner", own_bank="Synthetic revised bank")))
    assert database.q("select count(*) n,sum(amount) amount from flows where case_id=?", [cid]) == [
        {"n": 1, "amount": "10.25"}]
    assert database.q_raw("select * from flows where case_id=?", [cid]) == before
    assert changed["stage"] == "done" and changed["result"]["ok"] == 0
    assert changed["result"]["feedback"]["unresolved"] == 1


def test_dedup_binds_used_identity_without_reimporting_unrelated_auxiliary_changes(client, database, make_case):
    cid = make_case("Identity evidence")
    first = complete(client, cid, preview(client, cid, archive([transaction()])))
    assert first["stage"] == "done" and first["result"]["ok"] == 1
    unrelated = complete(client, cid, preview(client, cid, archive([transaction()], account_remark="Changed auxiliary note")))
    assert unrelated["stage"] == "done" and (unrelated["result"]["ok"], unrelated["result"]["rows_dup"]) == (0, 1)
    changed_view = preview(client, cid, archive([transaction()], owner_name="Synthetic revised owner", own_bank="Synthetic revised bank"))
    assert changed_view["preview"] == [] and changed_view["feedback"]["unresolved"] == 1
    assert changed_view["feedback"]["currency_totals"] == {}
    assert changed_view["stats"]["bad_examples"][0]["reason"] == "own_identity_conflict"
    changed = complete(client, cid, changed_view)
    assert changed["stage"] == "done" and changed["result"]["ok"] == 0
    assert (changed["done"], changed["total"]) == (2, 2)
    assert changed["result"]["feedback"]["reason_counts"] == {"own_identity_conflict": 1, "auxiliary_table": 1}
    new_source = changed["result"]["source_id"]
    linked = database.q("""select f.payer_name,f.payer_bank from flow_sources s join flows f on s.flow_id=f.id
                             where s.case_id=? and s.source_id=?""", [cid, new_source])
    assert linked == []
    record = client.get(f"/api/v1/cases/{cid}/imports/{new_source}/records").json()["items"][0]
    assert record["disposition"] == "unresolved" and record["reason_code"] == "own_identity_conflict"
    assert record["details"]["financial_effect"] == "undetermined"
    assert record["details"]["canonical_amount"] == "10.25"
    assert not {"own_identity_sha256", "own_identity_record"} & record["details"].keys()
    # Use actual parser/service output, not a seeded journal, for portability.
    exported = case_packages.export_package(cid)[0]
    restored = case_packages.import_package(exported)["case_id"]
    assert database.q("select count(*) n from import_records where case_id=?", [restored]) == [{"n": 6}]
    assert database.q("select count(*) n,sum(amount) amount from flows where case_id=?", [restored]) == [{"n": 1, "amount": "10.25"}]
    restored_original = complete(client, restored, preview(client, restored, archive([transaction()])))
    assert (restored_original["result"]["ok"], restored_original["result"]["rows_dup"]) == (0, 1)


@pytest.mark.parametrize("changed_identity", [
    {"owner_name": "Synthetic changed owner"}, {"own_bank": "Synthetic changed bank"},
    {"owner_name": "", "own_bank": ""},
])
def test_repeated_conflicts_preserve_money_and_original_identity_can_reuse_it(client, database, make_case, changed_identity):
    cid = make_case("Repeated identity conflict")
    original = archive([transaction()])
    complete(client, cid, preview(client, cid, original))
    before = database.q_raw("select * from flows where case_id=?", [cid])
    changed_content = archive([transaction()], **changed_identity)
    for _ in range(2):
        job = complete(client, cid, preview(client, cid, changed_content))
        assert (job["result"]["ok"], job["result"]["rows_dup"]) == (0, 0)
        assert job["result"]["feedback"]["unresolved"] == 1
        assert job["result"]["feedback"]["currency_totals"] == {}
        assert database.q_raw("select * from flows where case_id=?", [cid]) == before
        assert client.get(f"/api/v1/cases/{cid}/sources/{job['result']['source_id']}/file").content == changed_content
    restored = complete(client, cid, preview(client, cid, original))
    assert (restored["result"]["ok"], restored["result"]["rows_dup"]) == (0, 1)
    assert database.q_raw("select * from flows where case_id=?", [cid]) == before


def test_identity_guard_is_case_bound_and_preserves_distinct_source_rows(client, database, make_case):
    cid, other = make_case("Guard source"), make_case("Separate physical rows")
    complete(client, cid, preview(client, cid, archive([transaction(), transaction()])))
    second = complete(client, other, preview(client, other, archive(
        [transaction(), transaction()], owner_name="Synthetic other owner")))
    assert second["result"]["ok"] == 2 and second["result"]["feedback"]["unresolved"] == 0
    assert database.q("select count(*) n,sum(amount) amount from flows where case_id=?", [other]) == [
        {"n": 2, "amount": "20.50"}]
    changed = complete(client, other, preview(client, other, archive(
        [transaction(), transaction()], owner_name="Synthetic changed owner")))
    assert changed["result"]["ok"] == 0 and changed["result"]["feedback"]["unresolved"] == 2
    assert database.q("select count(*) n,sum(amount) amount from flows where case_id=?", [other]) == [
        {"n": 2, "amount": "20.50"}]


def test_commit_rechecks_identity_after_another_preview_wins(client, database, make_case):
    cid = make_case("Concurrent previews")
    first_view = preview(client, cid, archive([transaction()]))
    second_view = preview(client, cid, archive([transaction()], owner_name="Synthetic concurrent owner"))
    assert first_view["stats"]["ok"] == second_view["stats"]["ok"] == 1
    assert complete(client, cid, first_view)["result"]["ok"] == 1
    second = complete(client, cid, second_view)
    assert second["result"]["ok"] == 0 and second["result"]["feedback"]["unresolved"] == 1
    assert second["result"]["feedback"]["currency_totals"] == {}
    assert database.q("select count(*) n,sum(amount) amount from flows where case_id=?", [cid]) == [
        {"n": 1, "amount": "10.25"}]


def test_identity_binding_recounts_mixed_currency_and_existing_unresolved_rows(client, database, make_case):
    cid = make_case("Mixed binding counts")
    rows = [transaction(), transaction(**{"交易账号": "SYN-C", "交易币种": "USD", "交易金额": "20.50"}),
            transaction(**{"交易金额": "-1.00", "交易币种": "EUR"})]
    complete(client, cid, preview(client, cid, archive(rows)))
    changed_view = preview(client, cid, archive(rows, owner_name="Synthetic changed owner"))
    assert changed_view["stats"]["ok"] == 1 and changed_view["stats"]["skipped"] == 2
    assert changed_view["feedback"]["unresolved"] == 2
    assert changed_view["feedback"]["currency_totals"] == {"USD": "20.50"}
    assert changed_view["stats"]["currency_direction_totals"] == {"USD": {"in": "0.00", "out": "20.50"}}
    assert {row["reason"] for row in changed_view["stats"]["bad_examples"]} == {"negative_unknown", "own_identity_conflict"}
    repreview = client.post(f"/api/v1/cases/{cid}/imports/{changed_view['import_id']}/preview",
                           json={"mode": "B", "mapping": changed_view["mapping"]})
    assert repreview.status_code == 200 and repreview.json()["feedback"] == changed_view["feedback"]
    changed = complete(client, cid, changed_view)
    assert (changed["result"]["ok"], changed["result"]["rows_dup"]) == (0, 1)
    assert changed["result"]["feedback"]["currency_totals"] == {"USD": "20.50"}
    assert database.q("select currency,sum(amount) amount from flows where case_id=? group by currency order by currency", [cid]) == [
        {"currency": "CNY", "amount": "10.25"}, {"currency": "USD", "amount": "20.50"}]


@pytest.mark.parametrize("prior_disposition", ["imported", "future_active_posting"])
def test_unknown_prior_identity_namespace_cannot_add_the_same_money_again(client, database, make_case, prior_disposition):
    cid = make_case("Unknown prior namespace")
    content = archive([transaction()])
    first = complete(client, cid, preview(client, cid, content))
    source_id = first["result"]["source_id"]
    future_key = hashlib.sha256(b"synthetic future record namespace").hexdigest()
    database.x("update import_sources set parser_version='funds-trace-bank-feedback-v99' where source_id=?", [source_id])
    database.x("update import_records set record_key=?,disposition=? where source_id=? and record_no=1", [future_key, prior_disposition, source_id])
    before = database.q_raw("select * from flows where case_id=?", [cid])
    current = complete(client, cid, preview(client, cid, content))
    assert current["result"]["ok"] == 0 and current["result"]["feedback"]["unresolved"] == 1
    assert current["result"]["feedback"]["reason_counts"]["own_identity_conflict"] == 1
    assert database.q_raw("select * from flows where case_id=?", [cid]) == before
    assert database.q("select parser_version from import_sources where source_id=?", [source_id]) == [
        {"parser_version": "funds-trace-bank-feedback-v99"}]
    assert database.q("select record_key from import_records where source_id=? and record_no=1", [source_id]) == [
        {"record_key": future_key}]


def test_deleted_old_flow_does_not_block_changed_identity_reimport(client, database, make_case):
    cid = make_case("Deleted original occurrence")
    first = complete(client, cid, preview(client, cid, archive([transaction()])))
    changed_view = preview(client, cid, archive([transaction()], owner_name="Synthetic replacement identity"))
    assert changed_view["feedback"]["unresolved"] == 1
    assert client.delete(f"/api/batches/{first['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    after_delete = complete(client, cid, changed_view)
    assert after_delete["result"]["ok"] == 1 and after_delete["result"]["feedback"]["unresolved"] == 0
    assert database.q("select count(*) n,sum(amount) amount from flows where case_id=?", [cid]) == [
        {"n": 1, "amount": "10.25"}]


def test_conflict_journal_failure_rolls_back_only_the_new_operation(client, database, make_case, monkeypatch):
    cid = make_case("Conflict operation rollback")
    complete(client, cid, preview(client, cid, archive([transaction()])))
    before = database.q_raw("select * from flows where case_id=?", [cid])
    changed_view = preview(client, cid, archive([transaction()], own_bank="Synthetic changed bank"))
    original_insert = database.insert_dataframe
    def fail_journal(table, frame, columns):
        if table == "import_records":
            raise RuntimeError("Synthetic conflict journal failure")
        return original_insert(table, frame, columns)
    with monkeypatch.context() as failure:
        failure.setattr(database, "insert_dataframe", fail_journal)
        assert complete(client, cid, changed_view)["stage"] == "error"
    source_id = changed_view["import_id"]
    for table in ("import_sources", "flow_sources", "import_records", "import_members"):
        assert database.q(f"select count(*) n from {table} where source_id=?", [source_id]) == [{"n": 0}]
    assert database.q_raw("select * from flows where case_id=?", [cid]) == before
    assert database.q_raw("select result_json from import_sessions where import_id=?", [source_id]) == [{"result_json": None}]
    retry = complete(client, cid, changed_view)
    assert retry["stage"] == "done" and retry["result"]["feedback"]["unresolved"] == 1
    assert database.q_raw("select * from flows where case_id=?", [cid]) == before


def test_v2_surfaces_multiple_legacy_identity_interpretations_without_rewriting_them(client, database, make_case, monkeypatch):
    from app import feedback_import
    cid = make_case("Legacy identity interpretations")
    with monkeypatch.context() as legacy:
        legacy.setattr(feedback_import, "PROFILE", feedback_import.IDENTITY_PROFILE)
        complete(client, cid, preview(client, cid, archive([transaction()])))
        complete(client, cid, preview(client, cid, archive([transaction()], owner_name="Synthetic legacy alternative")))
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    assert len(before) == 2
    current = complete(client, cid, preview(client, cid, archive([transaction()])))
    assert current["result"]["ok"] == 0 and current["result"]["feedback"]["unresolved"] == 1
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before


def test_v1_upgrade_keeps_existing_facts_and_receipts_and_pins_preview_rules(client, database, make_case, monkeypatch):
    from app import feedback_import
    cid = make_case("Rules upgrade")
    rows = [transaction(), transaction(**{"摘要说明": "冲正", "交易流水号": "SYN-REV"}),
            transaction(**{"交易金额": "0.00"}), transaction(**{"交易金额": "-1.00"}),
            {"交易账号": "SYN-A", "查询反馈结果原因": "Synthetic query feedback"}]
    content = archive(rows, account=False)
    with monkeypatch.context() as old:
        old.setattr(feedback_import, "PROFILE", feedback_import.IDENTITY_PROFILE)
        v1 = preview(client, cid, content)
        first = complete(client, cid, v1)
    assert first["result"]["ok"] == 1 and first["result"]["feedback"]["review"] == 4
    before = database.q_raw("select * from flows where case_id=?", [cid])
    old_records = database.q_raw("select * from import_records where source_id=? order by record_no", [v1["import_id"]])
    with monkeypatch.context() as v2_default:
        v2_default.setattr(feedback_import, "PROFILE", feedback_import.PREVIOUS_PROFILE)
        v2 = preview(client, cid, content)
    assert v2["feedback"]["profile"] == "funds-trace-bank-feedback-v2"
    # Even a different running default must not change an already previewed job.
    with monkeypatch.context() as changed_default:
        changed_default.setattr(feedback_import, "PROFILE", feedback_import.IDENTITY_PROFILE)
        upgraded = complete(client, cid, v2)
    assert upgraded["stage"] == "done"
    assert upgraded["result"]["ok"] == 1 and upgraded["result"]["rows_dup"] == 1
    assert (upgraded["done"], upgraded["total"]) == (5, 5)
    assert upgraded["result"]["feedback"]["non_monetary"] == upgraded["result"]["feedback"]["unresolved"] == 1
    assert database.q_raw("select * from flows where case_id=? order by id", [cid])[0] == before[0]
    assert database.q_raw("select * from import_records where source_id=? order by record_no", [v1["import_id"]]) == old_records
    assert client.get(f"/api/v1/cases/{cid}/imports/{v1['import_id']}").json()["result"] == first["result"]
    assert complete(client, cid, preview(client, cid, content))["result"]["ok"] == 0
    history = client.get(f"/api/v1/cases/{cid}/imports").json()
    assert all("_feedback_profile" not in (item["request"] or {}) for item in history["items"])
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]["unresolved_records"] == 1


def test_zero_only_and_feedback_only_are_durable_automatic_results(client, database, make_case):
    cid = make_case("No monetary movements")
    rows = [transaction(**{"交易金额": "-0.00", "收付标志": "", "交易时间": "bad"}),
            {"交易账号": "SYN-A", "查询反馈结果原因": "Synthetic no detail"}]
    content = archive(rows, account=False)
    view = preview(client, cid, content)
    job = complete(client, cid, view)
    assert job["stage"] == "done" and (job["done"], job["total"]) == (2, 2)
    assert job["result"]["ok"] == job["result"]["feedback"]["unresolved"] == 0
    assert job["result"]["feedback"]["non_monetary"] == job["result"]["feedback"]["non_transaction"] == 1
    assert client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records", params={"disposition": "non_monetary"}).json()["total"] == 1
    database.x("update import_sessions set expires_at=?", [dt.datetime(2020, 1, 1)])
    assert imports.cleanup_uploads(cid, dry_run=False)["protected"] >= 1
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == content
    assert database.q("select count(*) n from flows where case_id=?", [cid]) == [{"n": 0}]
    packaged = case_packages.export_package(cid)[0]
    restored = case_packages.import_package(packaged)["case_id"]
    assert database.q("select disposition from import_records where case_id=? order by record_no", [restored]) == [
        {"disposition": "non_monetary"}, {"disposition": "non_transaction"}]


def test_unpinned_legacy_ready_and_old_receipt_remain_compatible(client, database, make_case):
    cid = make_case("Unpinned historical session")
    view = preview(client, cid, archive([transaction(**{"摘要说明": "冲正"})], account=False))
    database.x("update import_sessions set request_json=null where import_id=?", [view["import_id"]])
    job = complete(client, cid, view)
    assert job["result"]["ok"] == 0
    assert job["result"]["feedback"]["profile"] == "funds-trace-bank-feedback-v1"
    assert job["result"]["feedback"]["review"] == 1
    old = json.loads(database.q_raw("select result_json from import_sessions where import_id=?", [view["import_id"]])[0]["result_json"])
    for key in ("non_monetary", "unresolved", "unresolved_examples"):
        old["feedback"].pop(key)
    old_text = json.dumps(old)
    database.x("update import_sessions set result_json=? where import_id=?", [old_text, view["import_id"]])
    response = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}")
    assert response.status_code == 200
    assert response.json()["result"]["feedback"]["non_monetary"] == 0
    assert database.q_raw("select result_json from import_sessions where import_id=?", [view["import_id"]])[0]["result_json"] == old_text


def test_failed_v2_retry_preserves_rule_pin_and_atomicity(client, database, make_case, monkeypatch):
    from app import feedback_import
    cid = make_case("Pinned retry")
    with monkeypatch.context() as v2_default:
        v2_default.setattr(feedback_import, "PROFILE", feedback_import.PREVIOUS_PROFILE)
        view = preview(client, cid, archive([transaction(**{"摘要说明": "冲正"})], account=False))
    original = database.insert_dataframe
    def fail(table, frame, columns):
        if table == "import_records":
            raise RuntimeError("Synthetic journal interruption")
        return original(table, frame, columns)
    with monkeypatch.context() as failure:
        failure.setattr(database, "insert_dataframe", fail)
        assert complete(client, cid, view)["stage"] == "error"
    for table in ("flows", "batches", "flow_sources", "import_sources", "import_members", "import_records"):
        assert database.q(f"select count(*) n from {table} where case_id=?", [cid]) == [{"n": 0}]
    with monkeypatch.context() as different_default:
        different_default.setattr(feedback_import, "PROFILE", feedback_import.IDENTITY_PROFILE)
        job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    assert job["result"]["feedback"]["profile"] == "funds-trace-bank-feedback-v2"
    assert database.q("select count(*) n from flows where case_id=?", [cid]) == [{"n": 1}]


@pytest.mark.parametrize("future_change", ["profile", "disposition"])
def test_cleanup_preserves_journals_from_unrecognized_future_semantics(client, database, make_case, future_change):
    cid = make_case("Unknown future semantics")
    content = archive([], account=True)
    view = preview(client, cid, content)
    assert complete(client, cid, view)["stage"] == "done"
    if future_change == "profile":
        database.x("update import_sources set parser_version='funds-trace-bank-feedback-v99' where source_id=?", [view["import_id"]])
    else:
        database.x("update import_records set disposition='future_semantic_event' where source_id=?", [view["import_id"]])
    database.x("update import_sessions set expires_at=?", [dt.datetime(2020, 1, 1)])
    result = imports.cleanup_uploads(cid, dry_run=False)
    assert result["protected"] >= 1 and result["deleted"] == 0
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == content
