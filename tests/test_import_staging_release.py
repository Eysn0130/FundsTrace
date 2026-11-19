"""Release verified parser storage before the source ledger snapshot overlaps it."""

import pytest


@pytest.mark.parametrize("branch,profile", [("ordinary", "funds-trace-table-archive-v66"),
                                            ("evidence", "funds-trace-table-archive-v67")])
def test_verified_table_journal_releases_staging_before_ledger_read(client, database, make_case, monkeypatch, branch, profile):
    from app import import_validation, source_ledger
    from app.services import imports
    from test_archive_auxiliary_rows import preview, source
    from test_import_lifecycle_api import complete

    stores, phases = [], []
    bind = imports._bind_table_plan
    verify = import_validation.verify_table_write
    publish = source_ledger.commit

    def capture(case_id, parsed, **kwargs):
        result = bind(case_id, parsed, **kwargs)
        stores.append(parsed[0].attrs["table_plan"]["_plan_store"])
        return result

    def readback(*args):
        verify(*args)
        phases.append(("readback", stores[-1]._closed))

    def ledger(case_id, source_id, batch_id):
        phases.append(("ledger", stores[-1]._closed))
        return publish(case_id, source_id, batch_id)

    monkeypatch.setattr(imports, "_bind_table_plan", capture)
    monkeypatch.setattr(import_validation, "verify_table_write", readback)
    monkeypatch.setattr(source_ledger, "commit", ledger)
    payload = source(branch)
    cid = make_case("Synthetic verified staging release")
    view = preview(client, cid, payload)
    assert view["table_summary"]["profile"] == profile
    job = complete(client, cid, view)
    assert phases == [("readback", False), ("ledger", True)]

    receipt = job["result"]
    summary = receipt["table_summary"]
    assert summary == {**view["table_summary"], "effective_added": 2}
    assert (summary["record_total"], summary["accepted"], summary["unresolved"]) == (10, 2, 6)
    assert summary["currency_totals"] == {"CNY": "90071992547409.93", "USD": "2.03"}
    assert receipt["self_accounts"] == receipt["main_detected"] == [{"account": "000SYN-A", "name": ""}]
    source_id = view["import_id"]
    base = f"/api/v1/cases/{cid}/imports/{source_id}"
    assert client.get(base).json()["result"] == receipt
    assert client.get(base + "/records").json()["total"] == 10
    assert client.get(f"/api/v1/cases/{cid}/sources/{source_id}/file").content == payload
    assert database.q_raw("select source_row from source_ledger_records where case_id=? and source_id=? order by source_row",
                          [cid, source_id]) == [{"source_row": 2}, {"source_row": 3}]
