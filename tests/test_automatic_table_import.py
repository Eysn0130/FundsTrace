"""Public, synthetic upgrade regressions independent of parser expectations."""
import io
from decimal import Decimal
import zipfile

from openpyxl import Workbook
import pytest

from app import importer, table_archive, table_import
from app.services import imports
from test_feedback_import_api import complete


def preview(client, cid, content, filename="synthetic.csv"):
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": (filename, content)})
    assert response.status_code == 200, response.text
    return response.json()


def test_automatic_csv_units_conflicts_full_disposition_and_exact_coverage(client, database, make_case):
    cid = make_case("Synthetic automatic units")
    content = ("交易时间,本方账号,交易对手账卡号,交易金额(分),交易金额(万元),收付标志,交易币种\n"
               "2026-01-01 10:00:00,000SYN-A,000SYN-B,100,0.0001,出,CNY\n"
               "2026-01-01 11:00:00,000SYN-A,000SYN-C,900,0.001,出,CNY\n"
               "2026-01-01 12:00:00,000SYN-A,000SYN-D,200,0.0002,未知,CNY\n"
               "2026-01-01 13:00:00,000SYN-A,000SYN-E,0,0,出,CNY\n").encode()
    view = preview(client, cid, content)
    assert view["automatic"] is True
    summary = view["table_summary"]
    assert (summary["record_total"], summary["accepted"], summary["unresolved"], summary["non_monetary"]) == (4, 1, 2, 1)
    assert summary["currency_totals"] == {"CNY": "1.00"}
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert (job["done"], job["total"], job["result"]["ok"]) == (4, 4, 1)
    source = view["import_id"]
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert len(flows) == 1 and flows[0]["amount"] == "1.00" and flows[0]["payer_account"] == "000SYN-A"
    report = client.get(f"/api/v1/cases/{cid}/sources/{source}/report").json()
    assert [r["disposition"] for r in report["items"]] == ["imported", "unresolved", "unresolved", "non_monetary"]
    assert [r["source_row"] for r in report["items"]] == [2, 3, 4, 5]
    assert client.get(f"/api/v1/cases/{cid}/sources/{source}/file").content == content
    coverage = client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]
    assert coverage["unresolved_records"] == 2 and coverage["non_monetary_records"] == 1
    assert coverage["financial_interpretation_complete"] is False
    other = preview(client, cid, content, "same-facts.csv")
    repeated = complete(client, cid, other)
    assert repeated["result"]["ok"] == 0 and repeated["result"]["rows_dup"] == 1
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"] == coverage
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid])[0]["n"] == 1
    assert client.delete(f"/api/batches/{job['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    database.x("update import_sessions set expires_at=current_timestamp-interval '1 day' where case_id=?", [cid])
    cleanup = client.post(f"/api/v1/cases/{cid}/imports/cleanup", json={"dry_run": False}).json()
    assert cleanup["protected"] == 2 and cleanup["deleted"] == 0
    assert client.get(f"/api/v1/cases/{cid}/sources/{source}/file").content == content


def test_entirely_unknown_table_completes_without_mapping_or_fabricated_flows(client, database, make_case):
    cid = make_case("Synthetic unsupported structure")
    view = preview(client, cid, b"unknown_a,unknown_b\nSYN-A,SYN-B\n")
    assert view["automatic"] and view["mapping"] == {}
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 0
    summary = job["result"]["table_summary"]
    assert summary["record_total"] == summary["unresolved"] == 2
    assert summary["currency_totals"] == {}
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid])[0]["n"] == 0
    report = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()
    assert len(report["items"]) == 2 and all(r["flow_id"] is None for r in report["items"])


def test_xlsx_all_sheets_keep_distinct_internal_and_original_positions(client, make_case):
    cid = make_case("Synthetic workbook")
    workbook = Workbook()
    for index, title in enumerate(("Synthetic first", "Synthetic second")):
        sheet = workbook.active if index == 0 else workbook.create_sheet()
        sheet.title = title
        sheet.append(["date", "payer_account", "payee_account", "amount(cents)"])
        sheet.append([f"2026-01-0{index+1}", "000SYN-A", f"000SYN-{index}", str((index + 1) * 100)])
    buffer = io.BytesIO()
    workbook.save(buffer)
    view = preview(client, cid, buffer.getvalue(), "synthetic.xlsx")
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 2
    assert job["result"]["table_summary"]["currency_totals"] == {"CNY": "3.00"}
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    assert [r["record_no"] for r in records] == [1, 2] and [r["source_row"] for r in records] == [2, 2]
    assert len({r["member_path"] for r in records}) == 2
    for record in records:
        source = client.get(f"/api/v1/cases/{cid}/flows/{record['flow_id']}/sources").json()["items"][0]
        assert source["row_kind"] == "table_record" and source["member_row"] == 2
        assert source["source_row"] == record["record_no"]
        assert source["member_path"] == record["member_path"]


def test_table_preview_is_pinned_and_mapping_cannot_change_without_repreview(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic pinned table")
    view = preview(client, cid, b"date,payer_account,payee_account,amount(cents)\n2026-01-01,SYN-A,SYN-B,100\n")
    changed = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/commit",
                          json={"mode": "A", "mapping": {**view["mapping"], "summary": "amount(cents)"}})
    assert changed.status_code == 409
    with monkeypatch.context() as modified:
        modified.setattr(imports, "TABLE_PROFILE", importer.PARSER_VERSION)
        job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["table_summary"]["profile"] == table_import.PROFILE
    assert database.q_raw("select amount from flows where case_id=?", [cid])[0]["amount"] == 1


def test_historical_unpinned_session_keeps_v4_direction_contract(client, database, make_case):
    cid = make_case("Synthetic historical session")
    content = ("交易时间,本方账号,对方账号,交易金额,收入金额,支出金额,收付标志\n"
               "2026-01-01 10:00:00,SYN-A,SYN-B,1.01,0,9.99,收入\n").encode()
    view = preview(client, cid, content)
    assert view["table_summary"]["unresolved"] == 1
    # Recreate a historical ready session without the new internal version pin.
    database.x("update import_sessions set request_json=null where import_id=?", [view["import_id"]])
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    assert database.q_raw("select parser_version from import_sources where source_id=?", [view["import_id"]])[0]["parser_version"] == importer.PARSER_VERSION
    assert database.q_raw("select amount,payee_account from flows where case_id=?", [cid])[0] == {"amount": Decimal("1.01"), "payee_account": "SYN-A"}


def test_table_journal_failure_rolls_back_all_new_facts_then_retry(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic automatic rollback")
    view = preview(client, cid, b"date,payer_account,payee_account,amount\n2026-01-01,SYN-A,SYN-B,1.00\n")
    original = database.insert_dataframe

    def fail_after_journal(table, frame, columns):
        result = original(table, frame, columns)
        if table == "import_records":
            raise RuntimeError("Synthetic rollback")
        return result

    with monkeypatch.context() as failure:
        failure.setattr(database, "insert_dataframe", fail_after_journal)
        assert complete(client, cid, view)["stage"] == "error"
    for table in ("flows", "batches", "flow_sources", "import_sources", "import_members", "import_records"):
        assert database.q_raw(f"select count(*) n from {table} where case_id=?", [cid])[0]["n"] == 0
    assert complete(client, cid, view)["stage"] == "done"
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid])[0]["n"] == 1


def test_adding_nested_container_cannot_downgrade_old_feedback_reversal_policy(client, make_case):
    from test_cancellation_import_lifecycle import paired_source

    cid = make_case("Synthetic profile downgrade denial")
    leaf = io.BytesIO()
    with zipfile.ZipFile(leaf, "w") as package:
        package.writestr("unknown.csv", "unknown_a,unknown_b\nSYN-X,SYN-Y\n")
    mixed = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(paired_source())) as original, zipfile.ZipFile(mixed, "w") as package:
        for info in original.infolist():
            package.writestr(info.filename, original.read(info))
        package.writestr("extra.zip", leaf.getvalue())
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("synthetic.zip", mixed.getvalue())})
    assert response.status_code == 400
    assert "原规则" in response.json()["error"]["message"]


def test_feedback_envelope_keeps_distinct_source_events_but_normal_csv_retains_fact_dedup(client, make_case):
    content = (b"date,payer_account,payee_account,amount\n"
               b"2026-01-01,SYN-A,SYN-B,1.00\n2026-01-01,SYN-A,SYN-B,1.00\n")
    leaf = io.BytesIO()
    with zipfile.ZipFile(leaf, "w") as package:
        package.writestr("transactions.csv", content)
    envelope = io.BytesIO()
    with zipfile.ZipFile(envelope, "w") as package:
        package.writestr("nested.zip", leaf.getvalue())
    cid = make_case("Synthetic physical bank events")
    first = complete(client, cid, preview(client, cid, envelope.getvalue(), "synthetic.zip"))
    assert first["stage"] == "done" and first["result"]["ok"] == 2
    repeated = complete(client, cid, preview(client, cid, envelope.getvalue(), "renamed.zip"))
    assert repeated["result"]["ok"] == 0 and repeated["result"]["rows_dup"] == 2
    normal = make_case("Synthetic original table dedup")
    ordinary = complete(client, normal, preview(client, normal, content))
    assert ordinary["result"]["ok"] == 1 and ordinary["result"]["rows_dup"] == 1


@pytest.mark.parametrize("prior", ["ordinary", "envelope", "other_csv"])
def test_same_physical_csv_in_envelope_promotes_folded_rows_without_double_counting(client, make_case, prior):
    content = (b"date,payer_account,payee_account,amount\n"
               b"2026-01-01,SYN-A,SYN-B,1.00\n2026-01-01,SYN-A,SYN-B,1.00\n")
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as package:
        package.writestr("unknown.csv", b"unknown_a,unknown_b\nSYN-X,SYN-Y\n")
    envelope = io.BytesIO()
    with zipfile.ZipFile(envelope, "w") as package:
        package.writestr("table.csv", content)
        package.writestr("nested.zip", nested.getvalue())
    cid = make_case("Synthetic physical-source promotion")
    if prior == "other_csv":
        other = content.replace(b",amount\n", b",amount(yuan)\n")
        assert complete(client, cid, preview(client, cid, other))["result"]["ok"] == 1
    if prior != "envelope":
        ordinary = complete(client, cid, preview(client, cid, content))
        assert ordinary["result"]["ok"] == (0 if prior == "other_csv" else 1)
        assert ordinary["result"]["rows_dup"] == (2 if prior == "other_csv" else 1)
    view = preview(client, cid, envelope.getvalue(), "synthetic.zip")
    result = complete(client, cid, view)
    assert result["stage"] == "done"
    assert result["result"]["ok"] == (2 if prior == "envelope" else 1)
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    physical = [row for row in records if row["member_path"] == "table.csv"]
    assert len(physical) == 2 and len({row["flow_id"] for row in physical}) == 2
    for data, name in ((content, "synthetic.csv"), (envelope.getvalue(), "repeated.zip")):
        repeated = complete(client, cid, preview(client, cid, data, name))
        assert repeated["stage"] == "done" and repeated["result"]["ok"] == 0
        assert repeated["result"]["rows_dup"] == 2
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert len(flows) == 2 and sum(Decimal(row["amount"]) for row in flows) == Decimal("2.00")


@pytest.mark.parametrize("archive_profile", ["funds-trace-table-archive-v3", "funds-trace-table-archive-v4"],
                         ids=["frozen-v3", "default-v4"])
def test_envelope_replay_does_not_reuse_different_facts_from_manual_table_mapping(
        client, database, make_case, monkeypatch, archive_profile):
    import json
    from app import case_packages
    from test_archive_profile_upgrade import frozen_preview
    from test_feedback_package import _members
    from test_table_contract_lifecycle import facts
    from test_table_currency_region_lifecycle import receipt, source_snapshot
    from test_table_mapping_coverage import coverage

    content = b"date,payer_account,payee_account,amount\n2026-01-01,SYN-A,SYN-B,1.00\n"
    cid = make_case("Synthetic changed interpretation")
    view = preview(client, cid, content)
    assert view["table_summary"]["profile"] == "funds-trace-table-v17"
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={
        "mode": "A", "mapping": {**view["mapping"], "payer_account": "payee_account", "payee_account": "payer_account"}})
    assert response.status_code == 200, response.text
    first = complete(client, cid, response.json())
    assert first["stage"] == "done" and first["result"]["ok"] == 1
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    assert len(before) == 1
    assert (before[0]["payer_account"], before[0]["payee_account"]) == ("SYN-B", "SYN-A")
    frozen = source_snapshot(database, cid, view["import_id"])
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w"):
        pass
    envelope = io.BytesIO()
    with zipfile.ZipFile(envelope, "w") as package:
        package.writestr("table.csv", content)
        package.writestr("nested.zip", nested.getvalue())
    def archive_preview(case_id, filename):
        if archive_profile == "funds-trace-table-archive-v3":
            current = frozen_preview(client, case_id, envelope.getvalue(), monkeypatch, archive_profile)
        else:
            current = preview(client, case_id, envelope.getvalue(), filename)
        assert current["table_summary"]["profile"] == archive_profile
        return current

    automatic = archive_preview(cid, "synthetic.zip")
    result = complete(client, cid, automatic)
    assert result["stage"] == "done", result
    records = client.get(f"/api/v1/cases/{cid}/imports/{automatic['import_id']}/records").json()["items"]
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert len(records) == 1 and records[0]["source_row"] == 2
    if archive_profile == "funds-trace-table-archive-v3":
        assert (result["result"]["ok"], result["result"]["rows_dup"]) == (1, 0)
        flow_id = records[0]["flow_id"]
        actual = next(row for row in flows if row["id"] == str(flow_id))
        assert actual["payer_account"] == "SYN-A" and actual["payee_account"] == "SYN-B"
        assert actual["id"] != str(before[0]["id"])
    else:
        assert (result["result"]["ok"], result["result"]["rows_dup"]) == (0, 0)
        assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before
        assert (records[0]["disposition"], records[0]["reason_code"], records[0]["flow_id"]) == (
            "unresolved", "conflicting_field", None)
        assert records[0]["details"]["counterparty_basis"] == table_import.ARCHIVE_HISTORY_CONFLICT_BASIS
        assert records[0]["details"]["financial_effect"] == "undetermined"
        assert "conflicting_field" in records[0]["details"]["validation_issues"].split(",")
        assert coverage(client, cid)["unresolved_records"] > 0
        assert coverage(client, cid)["financial_interpretation_complete"] is False
    repeated_view = archive_preview(cid, "repeat.zip")
    repeated = complete(client, cid, repeated_view)
    assert repeated["stage"] == "done" and repeated["result"]["ok"] == 0
    assert repeated["result"]["rows_dup"] == (1 if archive_profile == "funds-trace-table-archive-v3" else 0)
    if archive_profile == "funds-trace-table-archive-v4":
        repeated_records = client.get(
            f"/api/v1/cases/{cid}/imports/{repeated_view['import_id']}/records").json()["items"]
        assert (repeated_records[0]["disposition"], repeated_records[0]["reason_code"], repeated_records[0]["flow_id"]) == (
            "unresolved", "conflicting_field", None)
        assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before
        assert coverage(client, cid)["unresolved_records"] > 0
        assert coverage(client, cid)["financial_interpretation_complete"] is False
    assert database.q_raw("select * from flows where case_id=? and id=?", [cid, before[0]["id"]]) == before
    assert source_snapshot(database, cid, view["import_id"]) == frozen
    assert receipt(client, cid, view["import_id"]) == first["result"]
    archive = case_packages.export_package(cid)[0]
    assert json.loads(_members(archive)["meta.json"])["format"] == 4
    restored = case_packages.import_package(archive)["case_id"]
    if archive_profile == "funds-trace-table-archive-v3":
        assert len(client.get(f"/api/v1/cases/{restored}/flows").json()["items"]) == 2
    else:
        assert len(client.get(f"/api/v1/cases/{restored}/flows").json()["items"]) == 1
        assert facts(database, restored) == facts(database, cid)
        assert coverage(client, restored) == coverage(client, cid)
        restored_view = archive_preview(restored, "restored-repeat.zip")
        restored_job = complete(client, restored, restored_view)
        assert restored_job["stage"] == "done"
        assert (restored_job["result"]["ok"], restored_job["result"]["rows_dup"]) == (0, 0)
        restored_records = client.get(
            f"/api/v1/cases/{restored}/imports/{restored_view['import_id']}/records").json()["items"]
        assert (restored_records[0]["disposition"], restored_records[0]["reason_code"], restored_records[0]["flow_id"]) == (
            "unresolved", "conflicting_field", None)
        assert facts(database, restored) == facts(database, cid)
        assert coverage(client, restored)["unresolved_records"] > 0
        assert coverage(client, restored)["financial_interpretation_complete"] is False


@pytest.mark.parametrize("malformation", ["path", "depth", "duplicate"])
def test_new_envelope_rejects_unsafe_structure_before_writes(client, database, make_case, malformation):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as package:
        package.writestr("table.csv", b"date,payer_account,payee_account,amount\n2026-01-01,SYN-A,SYN-B,1.00\n")
    data = content.getvalue()
    depth = table_archive.MAX_DEPTH + 2 if malformation == "depth" else 1
    for number in range(depth):
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as package:
            package.writestr("../nested.zip" if malformation == "path" else f"nested{number}.zip", data)
            if malformation == "duplicate":
                with pytest.warns(UserWarning, match="Duplicate name"):
                    package.writestr(f"nested{number}.zip", data)
        data = content.getvalue()
    cid = make_case("Synthetic archive bounds")
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("synthetic.zip", data)})
    assert response.status_code == 400
    assert database.q_raw("select count(*) n from import_sessions where case_id=?", [cid])[0]["n"] == 0
