"""Fault injection through the public workflow, with independent stored facts."""
import copy
from decimal import Decimal

import pytest

from app import importer, import_preview_cache, table_archive, table_import
from app.services import imports
from test_archive_profile_upgrade import frozen_preview
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_cancellation_lifecycle import paired_source
from test_table_mapping_versions import profile_preview
from test_table_write_integrity import csv_data, envelope, persisted, row


def test_writer_cannot_mutate_its_input_to_change_the_readback_expectation(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic immutable write expectation")
    payload = b"date,payer_account,payee_account,amount\n2026-01-01,SYN-A,SYN-B,1.01\n"
    view = preview(client, cid, payload)
    commit = importer.commit_rows

    def change_input(filename, mode, rows, *args, **kwargs):
        rows[0]["amount"] = Decimal("9.99")
        return commit(filename, mode, rows, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(importer, "commit_rows", change_input)
        failed = complete(client, cid, view)
    assert failed["stage"] == "error" and failed["error_code"] == "import_integrity_failed"
    assert database.q_raw("select amount from flows where case_id=?", [cid]) == []
    assert database.q_raw("select source_id from import_sources where case_id=?", [cid]) == []
    assert complete(client, cid, view)["stage"] == "done"
    assert database.q_raw("select amount from flows where case_id=?", [cid]) == [{"amount": Decimal("1.01")}]


@pytest.mark.parametrize("profile", [table_archive.CANCELLATION_PROFILE, None], ids=["historical", "current"])
def test_inconsistent_cancellation_proof_cannot_hide_an_existing_original(
        client, database, make_case, monkeypatch, profile):
    cid = make_case("Synthetic cancellation proof preflight")
    _, payload = paired_source()
    previous = frozen_preview(client, cid, payload, monkeypatch, table_archive.REFERENCE_PROFILE)
    previous_job = complete(client, cid, previous)
    assert previous_job["stage"] == "done"
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    assert len(before) == 2
    view = (preview(client, cid, payload, "synthetic.zip") if profile is None else
            frozen_preview(client, cid, payload, monkeypatch, profile))
    assert view["table_summary"]["profile"] == (table_archive.PROFILE if profile is None else profile)
    import_preview_cache.discard(view["import_id"])
    parse = imports._parse
    hits = []

    def broken_proof(*args, **kwargs):
        parsed = parse(*args, **kwargs)
        plan = parsed[0].attrs["table_plan"]
        negative = next(record for record in plan["records"]
                        if record["details"].get("cancellation_role") == "cancellation")
        assert negative["details"]["cancellation_balance"] == "100.00"
        negative["details"]["cancellation_balance"] = "99.99"
        assert next(record for record in plan["records"]
                    if record["details"].get("cancellation_role") == "cancellation")["details"]["cancellation_balance"] == "99.99"
        hits.append(True)
        return parsed

    with monkeypatch.context() as context:
        context.setattr(imports, "_parse", broken_proof)
        failed = complete(client, cid, view)
    assert hits == [True]
    assert failed["stage"] == "error" and failed["error_code"] == "import_integrity_failed"
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before
    assert database.q_raw("select count(*) n from effective_flows where case_id=?", [cid])[0]["n"] == 2
    assert database.q_raw("select count(*) n from import_records where case_id=? and source_id=?",
                          [cid, view["import_id"]])[0]["n"] == 0
    assert client.get(f"/api/v1/cases/{cid}/imports/{previous['import_id']}").json()["result"] == previous_job["result"]
    assert complete(client, cid, view)["stage"] == "done"
    if profile is not None:
        assert database.q_raw("select count(*) n from effective_flows where case_id=?", [cid])[0]["n"] == 1
    else:
        assert database.q_raw("select amount from flows where case_id=? and amount<0", [cid]) == [{"amount": Decimal("-10.00")}]
    stats = client.get(f"/api/v1/cases/{cid}/stats").json()
    assert stats["flows"] == 1 and stats["amount_total"] == "1.00"
    assert database.q_raw("select * from flows where case_id=? and amount>0 order by id", [cid]) == before
    assert client.get(f"/api/v1/cases/{cid}/imports/{previous['import_id']}").json()["result"] == previous_job["result"]


@pytest.mark.parametrize("profile", [table_import.NATIVE31_PROFILE, None], ids=["historical", "current"])
def test_accepted_source_cannot_disappear_from_the_writer_input(client, database, make_case, monkeypatch, profile):
    cid = make_case("Synthetic complete writer manifest")
    payload = (b"date,payer_account,payee_account,amount\n"
               b"2026-01-01,SYN-A,SYN-B,1.01\n2026-01-02,SYN-A,SYN-C,2.02\n")
    view = (preview(client, cid, payload) if profile is None else
            profile_preview(client, cid, payload, monkeypatch, profile))
    assert view["table_summary"]["profile"] == (table_import.PROFILE if profile is None else profile)
    import_preview_cache.discard(view["import_id"])
    parse = imports._parse
    hits = []

    def lose_row(*args, **kwargs):
        parsed = list(parse(*args, **kwargs))
        original = list(parsed[4])
        assert len(original) == 2
        parsed[4] = original[:-1]
        parsed[0].attrs["table_plan"]["rows"] = parsed[4]
        hits.append((len(original), len(parsed[4])))
        return tuple(parsed)

    with monkeypatch.context() as context:
        context.setattr(imports, "_parse", lose_row)
        failed = complete(client, cid, view)
    assert hits == [(2, 1)]
    assert failed["stage"] == "error" and failed["error_code"] == "import_integrity_failed"
    assert database.q_raw("select amount from flows where case_id=?", [cid]) == []
    assert database.q_raw("select source_id from import_sources where case_id=?", [cid]) == []
    assert database.q_raw("select source_id from import_records where case_id=? and source_id=?", [cid, view["import_id"]]) == []
    assert complete(client, cid, view)["stage"] == "done"
    assert database.q_raw("select amount from flows where case_id=? order by amount", [cid]) == [
        {"amount": Decimal("1.01")}, {"amount": Decimal("2.02")}]


@pytest.mark.parametrize("damage", ["source_row", "digest", "encoding", "role", "row_key", "position"])
def test_post_binding_csv_identity_mismatch_fails_before_writes_and_is_retryable(
        client, database, make_case, monkeypatch, damage):
    cid = make_case("Synthetic post-binding CSV identity")
    view = preview(client, cid, envelope(csv_data([row()])), "synthetic.zip")
    before = persisted(database)
    bind, commit = imports._bind_table_plan, importer.commit_rows
    writes = []

    def corrupt(*args, **kwargs):
        result = bind(*args, **kwargs)
        parsed = args[1]
        plan = parsed[0].attrs["table_plan"]
        financial = parsed[4][0]
        record = plan["records"][financial["_source_row"] - 1]
        member = next(item for item in plan["members"] if item["member_path"] == record["member_path"])
        if damage == "source_row":
            record["source_row"] = 0
        elif damage == "digest":
            member["sha256"] = "0" * 64
        elif damage == "encoding":
            member["encoding"] = "invalid-encoding"
        elif damage == "role":
            member["role"] = "auxiliary"
        elif damage == "row_key":
            financial["_record_key"] = "SYN-WRONG-KEY"
        else:
            financial["_source_row"] = len(plan["records"]) + 1
        return result

    def observe(*args, **kwargs):
        writes.append(True)
        return commit(*args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(imports, "_bind_table_plan", corrupt)
        context.setattr(importer, "commit_rows", observe)
        failed = complete(client, cid, view)
    assert failed["stage"] == "error" and failed["error_code"] == "import_integrity_failed"
    assert writes == []
    assert persisted(database) == before
    assert complete(client, cid, view)["stage"] == "done"
    assert database.q_raw("select trade_no,amount from flows where case_id=?", [cid]) == [
        {"trade_no": "SYN-NEW", "amount": Decimal("123.45")}]


@pytest.mark.parametrize("profile", [table_archive.REFERENCE_PROFILE, None], ids=["historical", "current"])
def test_nonstaged_plan_retains_public_csv_identity_and_facts(
        client, database, make_case, monkeypatch, profile):
    from test_archive_profile_upgrade import source, transaction

    cid = make_case("Synthetic nonstaged preparation")
    _, payload = source([transaction("SYN-LIST", "SYN-LIST", "1.01")])
    view = (preview(client, cid, payload, "synthetic.zip") if profile is None else
            frozen_preview(client, cid, payload, monkeypatch, profile))
    import_preview_cache.discard(view["import_id"])
    parse = imports._parse
    prepared = []

    def in_memory(*args, **kwargs):
        parsed = list(parse(*args, **kwargs))
        plan = parsed[0].attrs["table_plan"]
        for kind in ("records", "rows", "cancelled_rows", "cancellations"):
            if kind in plan:
                plan[kind] = copy.deepcopy(list(plan[kind]))
        store = plan.pop("_plan_store", None)
        if store is not None:
            store.close()
        parsed[4] = plan["rows"]
        prepared.append(True)
        return tuple(parsed)

    with monkeypatch.context() as context:
        context.setattr(imports, "_parse", in_memory)
        job = complete(client, cid, view)
    assert prepared == [True]
    assert job["stage"] == "done", job
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (1, 0)
    assert database.q_raw("select trade_no,amount,payer_account,payee_account from flows where case_id=?", [cid]) == [
        {"trade_no": "SYN-LIST", "amount": Decimal("1.01"), "payer_account": "SYN-OWN", "payee_account": "SYN-COUNTER"}]
