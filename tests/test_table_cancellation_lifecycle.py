"""Synthetic public lifecycle of the independently versioned table cancellation rule."""
import datetime as dt

import pytest

from app import table_archive
from app.services import imports
from test_archive_profile_upgrade import frozen_preview, report, source, transaction
from test_automatic_table_import import preview
from test_feedback_import_api import complete


def paired_source():
    return source([
        transaction("SYN-WITNESS", "SYN-WITNESS", "1.00", **{"交易余额": "100.00", "商户名称": "Synthetic merchant"}),
        transaction("SYN-PAIR", "SYN-PAIR", "10.00", **{"交易时间": "2026-01-01 10:01:00", "交易余额": "90.00", "商户名称": "Synthetic merchant"}),
        transaction("SYN-PAIR", "SYN-PAIR", "-10.00", **{"交易时间": "2026-01-01 10:02:00", "交易余额": "100.00", "摘要说明": "冲正", "商户名称": "Synthetic merchant"}),
    ])


def import_version(client, cid, content, monkeypatch, profile):
    view = frozen_preview(client, cid, content, monkeypatch, profile)
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    return view, job


def count(database, table, cid):
    return database.q_raw(f"select count(*) n from {table} where case_id=?", [cid])[0]["n"]


def coverage(client, cid):
    return client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]


def manual_preview(client, cid, payload, field):
    view = preview(client, cid, payload)
    mapping = {**view["mapping"], "summary": field}
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview",
                           json={"mode": view["mode"], "mapping": mapping})
    assert response.status_code == 200, response.text
    return response.json()


def test_v2_upgrade_keeps_facts_and_receipt_but_excludes_effective_original(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic table cancellation upgrade")
    _, content = paired_source()
    old, old_job = import_version(client, cid, content, monkeypatch, table_archive.REFERENCE_PROFILE)
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    assert len(before) == 2 and count(database, "effective_flows", cid) == 2
    view = preview(client, cid, content, "synthetic.zip")
    assert view["table_summary"]["cancelled"] == 2
    assert view["table_summary"]["effective_added"] is None
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (0, 1)
    assert job["result"]["table_summary"]["effective_added"] == 0
    assert job["result"]["table_summary"]["excluded_flows"] == 1
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before
    assert count(database, "effective_flows", cid) == 1
    assert client.get(f"/api/v1/cases/{cid}/imports/{old['import_id']}").json()["result"] == old_job["result"]
    assert [r["disposition"] for r in report(client, cid, view)["items"]] == ["duplicate", "cancelled", "cancelled"]
    assert coverage(client, cid)["cancelled_records"] == 2
    assert coverage(client, cid)["financial_interpretation_complete"]


def test_fresh_pair_repeat_and_old_reimport_never_add_effective_money(client, database, make_case, monkeypatch):
    cid, other = make_case("Synthetic fresh table pair"), make_case("Synthetic table other case")
    _, content = paired_source()
    first = complete(client, cid, preview(client, cid, content, "synthetic.zip"))
    assert first["stage"] == "done"
    assert first["result"]["ok"] == first["result"]["table_summary"]["effective_added"] == 1
    assert count(database, "flows", cid) == count(database, "effective_flows", cid) == 1
    _, old = import_version(client, cid, content, monkeypatch, table_archive.REFERENCE_PROFILE)
    assert old["result"]["ok"] == 1 and old["result"]["table_summary"]["effective_added"] == 0
    assert old["result"]["table_summary"]["excluded_flows"] == 1
    assert count(database, "flows", cid) == 2 and count(database, "effective_flows", cid) == 1
    repeated = complete(client, cid, preview(client, cid, content, "repeat.zip"))
    assert repeated["result"]["ok"] == repeated["result"]["table_summary"]["effective_added"] == 0
    assert count(database, "flows", cid) == 2 and count(database, "effective_flows", cid) == 1
    import_version(client, other, content, monkeypatch, table_archive.REFERENCE_PROFILE)
    assert count(database, "effective_flows", other) == 2
    assert client.get(f"/api/v1/cases/{other}/imports/{first['import_id']}").status_code == 404


def test_conflicting_historical_manual_facts_revoke_pair_and_remain_a_gap(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic historical table mapping conflict")
    payload, content = paired_source()
    manual = manual_preview(client, cid, payload, "商户名称")
    assert complete(client, cid, manual)["stage"] == "done"
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    view = frozen_preview(client, cid, content, monkeypatch, table_archive.CANCELLATION_PROFILE)
    assert view["table_summary"]["cancelled"] == 0 and view["table_summary"]["unresolved"] == 2
    assert view["table_summary"]["reason_counts"]["cancellation_historical_conflict"] == 2
    assert complete(client, cid, view)["stage"] == "done"
    assert database.q_raw("select * from flows where case_id=? order by id limit ?", [cid, len(before)]) == before
    assert count(database, "effective_flows", cid) == count(database, "flows", cid)
    assert not coverage(client, cid)["financial_interpretation_complete"]
    import_version(client, cid, content, monkeypatch, table_archive.REFERENCE_PROFILE)
    assert coverage(client, cid)["unresolved_records"] >= 2


@pytest.mark.parametrize("profile,expected_gaps,expected_flows", [
    ("funds-trace-table-v5", 2, 3),
    ("funds-trace-table-v6", 3, 2),
    ("funds-trace-table-v7", 3, 2),
])
def test_manual_conflict_after_v3_is_disclosed_without_hiding_competing_facts(
        client, database, make_case, monkeypatch, profile, expected_gaps, expected_flows):
    cid = make_case("Synthetic later table mapping conflict")
    payload, content = paired_source()
    import_version(client, cid, content, monkeypatch, table_archive.CANCELLATION_PROFILE)
    with monkeypatch.context() as frozen:
        frozen.setattr(imports, "TABLE_PROFILE", profile)
        manual = manual_preview(client, cid, payload, "商户名称")
    assert complete(client, cid, manual)["stage"] == "done"
    # v6 retains the original witness instead of adding a second interpretation
    # of that payment; v5 remains an explicit historical compatibility case.
    assert count(database, "flows", cid) == expected_flows
    assert count(database, "effective_flows", cid) == count(database, "flows", cid)
    current = coverage(client, cid)
    assert current["unresolved_records"] == expected_gaps and not current["financial_interpretation_complete"]
    repeated = frozen_preview(client, cid, content, monkeypatch, table_archive.CANCELLATION_PROFILE)
    assert repeated["table_summary"]["cancelled"] == 0
    assert complete(client, cid, repeated)["stage"] == "done"
    assert coverage(client, cid)["unresolved_records"] == expected_gaps


@pytest.mark.parametrize("legacy_without_journal", [False, True])
def test_shared_ordinary_flow_with_an_uncancelled_occurrence_stays_effective(client, database, make_case, legacy_without_journal):
    cid = make_case("Synthetic shared flow occurrence")
    payload, content = paired_source()
    assert complete(client, cid, preview(client, cid, payload))["stage"] == "done"
    other_payload, _ = source([transaction("SYN-PAIR", "SYN-PAIR", "10.00", **{
        "交易时间": "2026-01-01 10:01:00", "交易余额": "90.00", "商户名称": "Synthetic merchant"})])
    duplicate = complete(client, cid, preview(client, cid, other_payload))
    assert duplicate["result"]["ok"] == 0 and duplicate["result"]["rows_dup"] == 1
    if legacy_without_journal:
        legacy_source = duplicate["result"]["source_id"]
        database.x("delete from import_records where case_id=? and source_id=?", [cid, legacy_source])
        database.x("delete from import_members where case_id=? and source_id=?", [cid, legacy_source])
        database.x("update import_sources set row_kind='csv_record',parser_version='funds-trace-table-v4' where source_id=?",
                   [legacy_source])
    assert count(database, "flows", cid) == 2
    job = complete(client, cid, preview(client, cid, content, "synthetic.zip"))
    assert job["stage"] == "done" and job["result"]["table_summary"]["cancelled"] == 2
    assert job["result"]["table_summary"]["excluded_flows"] == 0
    assert count(database, "effective_flows", cid) == 2


def test_commit_rechecks_historical_facts_added_after_preview(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic table commit binding")
    payload, content = paired_source()
    view = frozen_preview(client, cid, content, monkeypatch, table_archive.CANCELLATION_PROFILE)
    assert view["table_summary"]["cancelled"] == 2
    assert complete(client, cid, manual_preview(client, cid, payload, "商户名称"))["stage"] == "done"
    result = complete(client, cid, view)
    assert result["stage"] == "done" and result["result"]["table_summary"]["cancelled"] == 0
    assert result["result"]["table_summary"]["unresolved"] == 2


def test_historical_flow_linked_to_negative_leg_revokes_the_pair(client, database, make_case):
    cid = make_case("Synthetic historical negative occurrence")
    payload, content = paired_source()
    ordinary = preview(client, cid, payload)
    assert complete(client, cid, ordinary)["stage"] == "done"
    records = report(client, cid, ordinary)["items"]
    # Represent an older mapped interpretation that persisted positive money
    # for the physical negative occurrence. Its actual flow must remain intact.
    database.x("update import_records set source_row=?,record_key=? where source_id=? and record_no=2",
               [records[2]["source_row"], records[2]["record_key"], ordinary["import_id"]])
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    view = preview(client, cid, content, "synthetic.zip")
    assert view["table_summary"]["cancelled"] == 0
    assert view["table_summary"]["reason_counts"]["cancellation_historical_conflict"] == 2
    assert complete(client, cid, view)["stage"] == "done"
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before
    assert count(database, "effective_flows", cid) == 2


def test_empty_own_name_hash_matches_the_frozen_actual_fact(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic unnamed owner pair")
    _, content = source([
        transaction("SYN-WITNESS", "SYN-WITNESS", "1.00", **{"交易余额": "100.00", "交易户名": ""}),
        transaction("SYN-PAIR", "SYN-PAIR", "10.00", **{"交易时间": "2026-01-01 10:01:00", "交易余额": "90.00", "交易户名": ""}),
        transaction("SYN-PAIR", "SYN-PAIR", "-10.00", **{"交易时间": "2026-01-01 10:02:00", "交易余额": "100.00", "摘要说明": "冲正", "交易户名": ""}),
    ])
    import_version(client, cid, content, monkeypatch, table_archive.REFERENCE_PROFILE)
    view = preview(client, cid, content, "synthetic.zip")
    assert view["table_summary"]["cancelled"] == 2
    assert complete(client, cid, view)["stage"] == "done"
    assert count(database, "flows", cid) == 2 and count(database, "effective_flows", cid) == 1


def test_journal_failure_rolls_back_exclusion_and_cancelled_original_is_retained(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic table cancellation rollback")
    _, content = paired_source()
    import_version(client, cid, content, monkeypatch, table_archive.REFERENCE_PROFILE)
    tables = ("flows", "batches", "import_sources", "flow_sources", "import_members", "import_records")
    before = {table: count(database, table, cid) for table in tables}
    view = preview(client, cid, content, "synthetic.zip")
    write = database.insert_dataframe

    def failure(table, frame, columns):
        result = write(table, frame, columns)
        if table == "import_records":
            raise RuntimeError("Synthetic failure after table cancellation journal")
        return result

    with monkeypatch.context() as failed:
        failed.setattr(database, "insert_dataframe", failure)
        assert complete(client, cid, view)["stage"] == "error"
    assert before == {table: count(database, table, cid) for table in tables}
    assert count(database, "effective_flows", cid) == 2
    assert complete(client, cid, view)["stage"] == "done"
    database.x("update import_sessions set expires_at=? where case_id=?", [dt.datetime(2020, 1, 1), cid])
    cleaned = client.post(f"/api/v1/cases/{cid}/imports/cleanup", json={"dry_run": False})
    assert cleaned.status_code == 200 and cleaned.json()["protected"] == 2 and cleaned.json()["deleted"] == 0
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == content


def test_restart_does_not_replay_a_table_cancellation_write(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic table cancellation restart")
    _, content = paired_source()
    view = preview(client, cid, content, "synthetic.zip")
    original = imports._run
    with monkeypatch.context() as interrupted:
        interrupted.setattr(imports, "_run", lambda *_: None)
        response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/commit",
                               json={"mode": view["mode"], "mapping": view["mapping"]})
        assert response.status_code == 200
    assert count(database, "flows", cid) == 0
    database.x("update import_sessions set owner_token='synthetic-dead-owner' where import_id=?", [view["import_id"]])
    imports.recover_interrupted()
    assert count(database, "flows", cid) == count(database, "import_records", cid) == 0
    session = database.q_raw("select stage,error_code from import_sessions where import_id=?", [view["import_id"]])[0]
    assert session == {"stage": "error", "error_code": "import_interrupted"}
    assert original is imports._run
