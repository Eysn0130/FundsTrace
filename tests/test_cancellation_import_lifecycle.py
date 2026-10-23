"""Effective cancellations through import, history, rollback and portable cases."""
import io
import json

from fastapi import HTTPException
import pytest

from app import case_packages, feedback_import
from test_feedback_import_api import archive, complete, preview, transaction
from test_feedback_package import _archive, _members, _mutate


def paired_source(**options):
    return archive([
        transaction(**{"交易金额": "1.00", "交易余额": "100.00", "交易流水号": "SYN-WITNESS"}),
        transaction(**{"交易时间": "2026-01-01 10:01:00", "交易金额": "10.00",
                       "交易余额": "90.00", "交易流水号": "SYN-CANCEL-PAIR"}),
        transaction(**{"交易时间": "2026-01-01 10:02:00", "交易金额": "-10.00",
                       "交易余额": "100.00", "交易流水号": "SYN-CANCEL-PAIR", "摘要说明": "冲正", "备注": ""}),
    ], **options)


def import_version(client, cid, content, monkeypatch, version):
    with monkeypatch.context() as selected:
        selected.setattr(feedback_import, "PROFILE", version)
        view = preview(client, cid, content)
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    return view, job


def count(database, table, cid):
    return database.q_raw(f"select count(*) n from {table} where case_id=?", [cid])[0]["n"]


def test_v2_upgrade_excludes_original_without_rewriting_facts_receipts_or_revival(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic cancellation upgrade")
    content = paired_source()
    old_view, old_job = import_version(client, cid, content, monkeypatch, feedback_import.PREVIOUS_PROFILE)
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    assert len(before) == 2 and count(database, "effective_flows", cid) == 2

    with monkeypatch.context() as selected:
        selected.setattr(feedback_import, "PROFILE", feedback_import.CANCELLATION_PROFILE)
        view = preview(client, cid, content)
    assert view["feedback"]["cancelled"] == 2
    # Pinned v3 must remain v3 even if the application's default changes.
    with monkeypatch.context() as changed:
        changed.setattr(feedback_import, "PROFILE", feedback_import.IDENTITY_PROFILE)
        job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert job["result"]["ok"] == 0 and job["result"]["rows_dup"] == 1
    assert job["result"]["feedback"]["excluded_flows"] == 1
    assert (job["done"], job["total"]) == (4, 4)
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before
    assert count(database, "effective_flows", cid) == 1
    stats = client.get(f"/api/v1/cases/{cid}/stats").json()
    assert stats["flows"] == 1 and stats["amount_totals"] == [{"currency": "CNY", "amount": "1.00"}]
    assert stats["import_coverage"]["cancelled_records"] == 2
    assert stats["import_coverage"]["unresolved_records"] == 0
    assert client.get(f"/api/v1/cases/{cid}/imports/{old_view['import_id']}").json()["result"] == old_job["result"]
    report = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()
    assert [r["disposition"] for r in report["items"]] == ["duplicate", "cancelled", "cancelled", "auxiliary"]
    filtered = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records?disposition=cancelled")
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 2
    assert all(item["disposition"] == "cancelled" for item in filtered.json()["items"])
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == content
    for version in (feedback_import.IDENTITY_PROFILE, feedback_import.PREVIOUS_PROFILE, feedback_import.CANCELLATION_PROFILE):
        import_version(client, cid, content, monkeypatch, version)
        assert count(database, "effective_flows", cid) == 1
        assert count(database, "flows", cid) == 2


def test_fresh_cancellation_and_later_old_import_stay_excluded_and_case_scoped(
        client, database, make_case, monkeypatch):
    cid, other = make_case("Synthetic fresh"), make_case("Synthetic other")
    content = paired_source()
    _, first = import_version(client, cid, content, monkeypatch, feedback_import.CANCELLATION_PROFILE)
    assert first["result"]["ok"] == 1 and first["result"]["feedback"]["cancelled"] == 2
    assert count(database, "flows", cid) == count(database, "effective_flows", cid) == 1
    import_version(client, cid, content, monkeypatch, feedback_import.PREVIOUS_PROFILE)
    assert count(database, "flows", cid) == 2 and count(database, "effective_flows", cid) == 1
    import_version(client, other, content, monkeypatch, feedback_import.PREVIOUS_PROFILE)
    assert count(database, "effective_flows", other) == 2


def test_cancelled_journal_failure_rolls_back_exclusion_and_all_new_records(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic cancellation rollback")
    content = paired_source()
    import_version(client, cid, content, monkeypatch, feedback_import.PREVIOUS_PROFILE)
    before = {table: count(database, table, cid) for table in (
        "flows", "batches", "flow_sources", "import_sources", "import_members", "import_records")}
    view = preview(client, cid, content)
    write = database.insert_dataframe

    def fail_after_journal(table, frame, columns):
        result = write(table, frame, columns)
        if table == "import_records":
            raise RuntimeError("Synthetic interruption after cancellation journal")
        return result

    with monkeypatch.context() as failing:
        failing.setattr(database, "insert_dataframe", fail_after_journal)
        assert complete(client, cid, view)["stage"] == "error"
    assert before == {table: count(database, table, cid) for table in before}
    assert count(database, "effective_flows", cid) == 2
    assert complete(client, cid, view)["stage"] == "done"
    assert count(database, "effective_flows", cid) == 1


def test_changed_auxiliary_identity_cannot_cancel_an_existing_different_identity(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic cancellation identity")
    import_version(client, cid, paired_source(), monkeypatch, feedback_import.PREVIOUS_PROFILE)
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    view = preview(client, cid, paired_source(owner_name="Synthetic conflicting owner"))
    assert view["feedback"]["cancelled"] == 0 and view["feedback"]["unresolved"] == 3
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 0
    assert count(database, "effective_flows", cid) == 2
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before


def test_pinned_v1_with_changed_identity_cannot_revive_a_cancelled_original(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic legacy revival guard")
    import_version(client, cid, paired_source(), monkeypatch, feedback_import.CANCELLATION_PROFILE)
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    _, old_job = import_version(client, cid, paired_source(owner_name="Synthetic different owner"),
                               monkeypatch, feedback_import.IDENTITY_PROFILE)
    assert old_job["result"]["ok"] == 0
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before
    assert count(database, "effective_flows", cid) == 1
    package, _ = case_packages.export_package(cid)
    restored = case_packages.import_package(package)["case_id"]
    assert count(database, "effective_flows", restored) == 1


def test_cancellation_package_round_trip_preserves_effective_exclusion_and_ids_remap(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic portable cancellations")
    content = paired_source()
    import_version(client, cid, content, monkeypatch, feedback_import.PREVIOUS_PROFILE)
    import_version(client, cid, content, monkeypatch, feedback_import.CANCELLATION_PROFILE)
    package, _ = case_packages.export_package(cid)
    assert json.loads(_members(package)["meta.json"])["format"] == 3
    result = case_packages.import_package(package)
    restored = result["case_id"]
    assert restored != cid
    assert count(database, "flows", restored) == 2 and count(database, "effective_flows", restored) == 1
    assert client.get(f"/api/v1/cases/{restored}/stats").json()["import_coverage"]["cancelled_records"] == 2
    assert {r["id"] for r in database.q_raw("select id from flows where case_id=?", [cid])}.isdisjoint(
        r["id"] for r in database.q_raw("select id from flows where case_id=?", [restored]))


def test_legacy_identity_guard_is_member_scoped_and_package_stays_portable(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic member-scoped legacy guard")

    def content(owner_name):
        members = _members(paired_source(owner_name=owner_name))
        ordinary = archive([transaction(**{"交易流水号": "SYN-ORDINARY-MEMBER"})], account=False)
        members["synthetic/ordinary.csv"] = _members(ordinary)["synthetic/transactions.csv"]
        return _archive(members)

    import_version(client, cid, content("Synthetic owner"), monkeypatch, feedback_import.CANCELLATION_PROFILE)
    view, _ = import_version(client, cid, content("Synthetic changed owner"),
                             monkeypatch, feedback_import.IDENTITY_PROFILE)
    records = database.q_raw("select member_path,reason_code from import_records where case_id=? and source_id=?",
                            [cid, view["import_id"]])
    assert any(r["reason_code"] == "own_identity_conflict" for r in records
               if r["member_path"] == "synthetic/transactions.csv")
    assert all(r["reason_code"] != "own_identity_conflict" for r in records
               if r["member_path"] == "synthetic/ordinary.csv")
    package, _ = case_packages.export_package(cid)
    restored = case_packages.import_package(package)["case_id"]
    assert count(database, "effective_flows", restored) == count(database, "effective_flows", cid)


@pytest.mark.parametrize("change", ["missing_pair", "same_role", "wrong_amount", "both_wrong_amounts", "self_witness", "wrong_policy", "downgrade"])
def test_tampered_cancellation_package_is_rejected_before_case_creation(
        client, database, make_case, monkeypatch, tmp_path, change):
    cid = make_case("Synthetic invalid cancellation package")
    if change == "both_wrong_amounts":
        import_version(client, cid, paired_source(), monkeypatch, feedback_import.PREVIOUS_PROFILE)
    import_version(client, cid, paired_source(), monkeypatch, feedback_import.CANCELLATION_PROFILE)
    package, _ = case_packages.export_package(cid)
    members = _members(package)
    if change == "downgrade":
        meta = json.loads(members["meta.json"])
        meta["format"] = 2
        members["meta.json"] = json.dumps(meta).encode()
    else:
        sql = {
            "missing_pair": "delete from entries where record_no=3",
            "same_role": "update entries set details_json=json_merge_patch(details_json,'{\"cancellation_role\":\"original\"}') where record_no=3",
            "wrong_amount": "update entries set details_json=json_merge_patch(details_json,'{\"canonical_amount\":\"-9.00\",\"signed_amount\":\"-9.00\"}') where record_no=3",
            "both_wrong_amounts": "update entries set details_json=json_merge_patch(details_json,case when record_no=2 then '{\"canonical_amount\":\"9.00\",\"signed_amount\":\"9.00\"}' else '{\"canonical_amount\":\"-9.00\",\"signed_amount\":\"-9.00\"}' end) where disposition='cancelled'",
            "self_witness": "update entries set details_json=json_merge_patch(details_json,'{\"cancellation_witness_record_no\":\"2\"}') where disposition='cancelled'",
            "wrong_policy": "update entries set details_json=json_merge_patch(details_json,'{\"cancellation_policy\":\"unknown\"}') where disposition='cancelled'",
        }[change]
        _mutate(members, "import_records", sql, tmp_path)
    cases_before = database.q_raw("select * from cases order by id")
    with pytest.raises(HTTPException) as failure:
        case_packages.import_package(_archive(members))
    assert failure.value.status_code == 400
    assert database.q_raw("select * from cases order by id") == cases_before
