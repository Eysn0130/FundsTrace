"""A mapping upgrade must not turn one physical source row into two payments."""
import json
from decimal import Decimal

import pytest

from app import case_packages, table_import
from app.services import imports
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_mapping_versions import legacy_preview


PAYLOAD = ("date,payer_account,payee_account,付款方账户名称,amount\n"
           "2026-01-01,SYN-A,SYN-B,Synthetic payer,1.01\n").encode()


def stats(client, cid):
    return client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]


@pytest.mark.parametrize("newest_first", [False, True])
@pytest.mark.parametrize("current_profile", ["funds-trace-table-v6", "funds-trace-table-v7"])
def test_both_ready_version_orders_preserve_one_payment_and_original_receipt(
        client, database, make_case, monkeypatch, newest_first, current_profile):
    monkeypatch.setattr(imports, "TABLE_PROFILE", current_profile)
    cid = make_case("Synthetic physical history binding")
    old = legacy_preview(client, cid, PAYLOAD, monkeypatch)
    current = preview(client, cid, PAYLOAD)
    assert old["stats"]["ok"] == current["stats"]["ok"] == 1
    first, second = (current, old) if newest_first else (old, current)
    first_job = complete(client, cid, first)
    assert first_job["stage"] == "done" and first_job["result"]["ok"] == 1
    before = database.q_raw("select * from flows where case_id=?", [cid])
    rechecked = client.post(f"/api/v1/cases/{cid}/imports/{second['import_id']}/preview",
                            json={"mode": second["mode"], "mapping": second["mapping"]})
    assert rechecked.status_code == 200 and rechecked.json()["table_summary"]["unresolved"] == 1
    second_job = complete(client, cid, rechecked.json())
    assert second_job["stage"] == "done" and second_job["result"]["ok"] == 0
    assert second_job["result"]["table_summary"]["unresolved"] == 1
    assert database.q_raw("select * from flows where case_id=?", [cid]) == before
    assert database.q_raw("select sum(amount) amount from effective_flows where case_id=?", [cid]) == [{"amount": Decimal("1.01")}]
    assert client.get(f"/api/v1/cases/{cid}/imports/{first['import_id']}").json()["result"] == first_job["result"]
    record = client.get(f"/api/v1/cases/{cid}/sources/{second['import_id']}/report").json()["items"][0]
    assert record["disposition"] == "unresolved" and record["flow_id"] is None
    assert record["details"]["counterparty_basis"] == table_import.HISTORY_CONFLICT_BASIS
    # A stale v5 evaluation cannot demote a complete, actually stored v6 fact.
    assert stats(client, cid)["unresolved_records"] == (0 if newest_first else 1)
    assert stats(client, cid)["financial_interpretation_complete"] is newest_first
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert database.q_raw("select sum(amount) amount from effective_flows where case_id=?", [restored]) == [{"amount": Decimal("1.01")}]
    assert stats(client, restored) == stats(client, cid)


@pytest.mark.parametrize("current_profile", ["funds-trace-table-v6", "funds-trace-table-v7"])
def test_commit_rechecks_history_written_after_an_accepted_preview(client, database, make_case, monkeypatch, current_profile):
    monkeypatch.setattr(imports, "TABLE_PROFILE", current_profile)
    cid = make_case("Synthetic commit history race")
    old = legacy_preview(client, cid, PAYLOAD, monkeypatch)
    current = preview(client, cid, PAYLOAD)
    assert current["stats"]["ok"] == 1
    assert complete(client, cid, old)["result"]["ok"] == 1
    # Commit without a repreview: final transaction must discover the old fact.
    job = complete(client, cid, current)
    assert job["stage"] == "done" and job["result"]["ok"] == 0
    assert job["result"]["table_summary"]["unresolved"] == 1
    assert database.q_raw("select count(*) n,sum(amount) amount from flows where case_id=?", [cid]) == [
        {"n": 1, "amount": Decimal("1.01")}]


@pytest.mark.parametrize("current_profile", ["funds-trace-table-v6", "funds-trace-table-v7"])
def test_role_explicit_repreview_cannot_duplicate_an_existing_physical_payment(client, database, make_case, monkeypatch, current_profile):
    monkeypatch.setattr(imports, "TABLE_PROFILE", current_profile)
    cid = make_case("Synthetic advanced mapping history")
    payload = b"date,payer_account,payee_account,amount,memo,channel\n2026-01-01,SYN-A,SYN-B,1.01,SYN-MEMO,SYN-CHANNEL\n"
    first = preview(client, cid, payload)
    assert complete(client, cid, first)["stage"] == "done"
    view = preview(client, cid, payload)
    changed = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview",
                          json={"mode": view["mode"], "mapping": {**view["mapping"], "summary": "channel"}})
    assert changed.status_code == 200 and changed.json()["table_summary"]["unresolved"] == 1
    assert complete(client, cid, changed.json())["stage"] == "done"
    assert database.q_raw("select amount,summary from flows where case_id=?", [cid]) == [{"amount": Decimal("1.01"), "summary": "SYN-MEMO"}]


def test_history_binding_is_case_and_member_digest_scoped(client, database, make_case, monkeypatch):
    cid, other = make_case("Synthetic history scope"), make_case("Synthetic other history scope")
    assert complete(client, cid, legacy_preview(client, cid, PAYLOAD, monkeypatch))["result"]["ok"] == 1
    assert complete(client, other, preview(client, other, PAYLOAD))["result"]["ok"] == 1
    different = PAYLOAD.replace(b"1.01", b"2.02")
    assert complete(client, cid, preview(client, cid, different))["result"]["ok"] == 1
    assert database.q_raw("select amount from flows where case_id=? order by amount", [cid]) == [
        {"amount": Decimal("1.01")}, {"amount": Decimal("2.02")}]


def test_deleted_history_no_longer_blocks_a_current_interpretation(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic removed history")
    old_job = complete(client, cid, legacy_preview(client, cid, PAYLOAD, monkeypatch))
    current = preview(client, cid, PAYLOAD)
    assert current["table_summary"]["unresolved"] == 1
    assert client.delete(f"/api/batches/{old_job['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    job = complete(client, cid, current)
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    record = database.q_raw("select amount,payer_name from flows where case_id=?", [cid])[0]
    assert record == {"amount": Decimal("1.01"), "payer_name": "Synthetic payer"}
    details = json.loads(database.q_raw("select details_json from import_records where case_id=? and source_id=?",
                                       [cid, current["import_id"]])[0]["details_json"])
    assert details.get("counterparty_basis") != table_import.HISTORY_CONFLICT_BASIS
