"""A new identity contradiction cannot be hidden by older envelope assessments."""
import io
import json
import zipfile
from decimal import Decimal

from fastapi import HTTPException
import pytest

from app import case_packages, table_import
from test_archive_profile_upgrade import source, transaction
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_mapping_versions import legacy_preview, profile_preview


def envelope(payload):
    empty, output = io.BytesIO(), io.BytesIO()
    with zipfile.ZipFile(empty, "w"):
        pass
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("table.csv", payload)
        package.writestr("nested.zip", empty.getvalue())
    return output.getvalue()


def coverage(client, cid):
    return client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]


@pytest.mark.parametrize("first", ["archive", "ordinary"])
@pytest.mark.parametrize("profile", ["funds-trace-table-v6", "funds-trace-table-v7"])
@pytest.mark.parametrize("kind", ["layouts", "candidates"])
def test_identity_gap_survives_old_envelope_precedence_and_package(client, database, make_case, monkeypatch, first, profile, kind):
    cid = make_case("Synthetic mapping gap precedence")
    if kind == "layouts":
        payload = (b"date,payer_account,payee_account,account,counterparty account,direction,amount\n"
                   b"2026-01-01,SYN-A,SYN-B,SYN-A,SYN-X,out,1.01\n")
    else:
        payload = ("date,payer_account,payee_account,account,counterparty account number,对手方账户号码,direction,amount\n"
                   "2026-01-01,SYN-A,SYN-B,SYN-A,SYN-B,SYN-X,out,1.01\n").encode()
    archive_payload = envelope(payload)
    for kind_to_import in (first, "ordinary" if first == "archive" else "archive"):
        view = (preview(client, cid, archive_payload, "synthetic.zip") if kind_to_import == "archive"
                else profile_preview(client, cid, payload, monkeypatch, profile))
        job = complete(client, cid, view)
        assert job["stage"] == "done"
        if kind_to_import == "archive":
            assert job["result"]["ok"] == 1
            old_source, old_receipt = view["import_id"], job["result"]
        else:
            assert job["result"]["table_summary"]["unresolved"] == 1
    expected = database.q_raw("select * from flows where case_id=?", [cid])
    assert len(expected) == 1 and expected[0]["amount"] == Decimal("1.01")
    assert coverage(client, cid)["unresolved_records"] == 1
    assert not coverage(client, cid)["financial_interpretation_complete"]
    repeated = complete(client, cid, preview(client, cid, archive_payload, "repeat.zip"))
    assert repeated["stage"] == "done" and repeated["result"]["ok"] == 0
    assert database.q_raw("select * from flows where case_id=?", [cid]) == expected
    assert coverage(client, cid)["unresolved_records"] == 1
    assert client.get(f"/api/v1/cases/{cid}/imports/{old_source}").json()["result"] == old_receipt
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert coverage(client, restored)["unresolved_records"] == 1
    assert not coverage(client, restored)["financial_interpretation_complete"]


@pytest.mark.parametrize("profile", ["funds-trace-table-v6", "funds-trace-table-v7"])
def test_ordinary_reference_ambiguity_does_not_override_known_bank_reference_policy(client, database, make_case, monkeypatch, profile):
    cid = make_case("Synthetic bank reference precedence")
    payload, archive_payload = source([transaction()])
    accepted = complete(client, cid, preview(client, cid, archive_payload, "synthetic.zip"))
    assert accepted["stage"] == "done" and accepted["result"]["ok"] == 1
    ambiguous_view = profile_preview(client, cid, payload, monkeypatch, profile)
    job = complete(client, cid, ambiguous_view)
    assert job["stage"] == "done" and job["result"]["table_summary"]["unresolved"] == 1
    details = json.loads(database.q_raw("select details_json from import_records where case_id=? and source_id=?",
                                       [cid, ambiguous_view["import_id"]])[0]["details_json"])
    assert details.get("counterparty_basis") != table_import.ROLE_CONFLICT_BASIS
    assert details.get("validation_basis") != table_import.FINANCIAL_CONFLICT_BASIS
    assert coverage(client, cid)["unresolved_records"] == 0
    assert coverage(client, cid)["financial_interpretation_complete"]


def test_package_rejects_role_conflict_marker_on_historical_accepted_facts(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic invalid role marker")
    payload = b"date,payer_account,payee_account,amount\n2026-01-01,SYN-A,SYN-B,1.01\n"
    view = legacy_preview(client, cid, payload, monkeypatch)
    assert complete(client, cid, view)["stage"] == "done"
    record = database.q_raw("select details_json from import_records where case_id=?", [cid])[0]
    details = json.loads(record["details_json"])
    details["counterparty_basis"] = table_import.ROLE_CONFLICT_BASIS
    database.x("update import_records set details_json=? where case_id=?", [json.dumps(details), cid])
    with pytest.raises(HTTPException, match="身份冲突标记无效"):
        case_packages.import_package(case_packages.export_package(cid)[0])
