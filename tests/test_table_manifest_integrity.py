"""Fault injection through the public workflow, with independent stored facts."""
from decimal import Decimal

from app import importer, table_archive
from app.services import imports
from test_archive_profile_upgrade import frozen_preview
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_cancellation_lifecycle import paired_source


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


def test_inconsistent_cancellation_proof_cannot_hide_an_existing_original(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic cancellation proof preflight")
    _, payload = paired_source()
    previous = frozen_preview(client, cid, payload, monkeypatch, table_archive.REFERENCE_PROFILE)
    previous_job = complete(client, cid, previous)
    assert previous_job["stage"] == "done"
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    assert len(before) == 2
    view = preview(client, cid, payload, "synthetic.zip")
    parse = imports._parse

    def broken_proof(*args, **kwargs):
        parsed = parse(*args, **kwargs)
        plan = parsed[0].attrs["table_plan"]
        negative = next(record for record in plan["records"]
                        if record["details"].get("cancellation_role") == "cancellation")
        negative["details"]["cancellation_balance"] = "99.99"
        return parsed

    with monkeypatch.context() as context:
        context.setattr(imports, "_parse", broken_proof)
        failed = complete(client, cid, view)
    assert failed["stage"] == "error" and failed["error_code"] == "import_integrity_failed"
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before
    assert database.q_raw("select count(*) n from effective_flows where case_id=?", [cid])[0]["n"] == 2
    assert database.q_raw("select count(*) n from import_records where case_id=? and source_id=?",
                          [cid, view["import_id"]])[0]["n"] == 0
    assert client.get(f"/api/v1/cases/{cid}/imports/{previous['import_id']}").json()["result"] == previous_job["result"]
    assert complete(client, cid, view)["stage"] == "done"
    assert database.q_raw("select count(*) n from effective_flows where case_id=?", [cid])[0]["n"] == 1


def test_accepted_source_cannot_disappear_from_the_writer_input(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic complete writer manifest")
    payload = (b"date,payer_account,payee_account,amount\n"
               b"2026-01-01,SYN-A,SYN-B,1.01\n2026-01-02,SYN-A,SYN-C,2.02\n")
    view = preview(client, cid, payload)
    parse = imports._parse

    def lose_row(*args, **kwargs):
        parsed = parse(*args, **kwargs)
        parsed[4].pop()
        return parsed

    with monkeypatch.context() as context:
        context.setattr(imports, "_parse", lose_row)
        failed = complete(client, cid, view)
    assert failed["stage"] == "error" and failed["error_code"] == "import_integrity_failed"
    assert database.q_raw("select amount from flows where case_id=?", [cid]) == []
    assert complete(client, cid, view)["stage"] == "done"
    assert database.q_raw("select amount from flows where case_id=? order by amount", [cid]) == [
        {"amount": Decimal("1.01")}, {"amount": Decimal("2.02")}]
