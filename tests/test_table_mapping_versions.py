"""Public version pins and packages retain both deterministic interpretations."""
import io
from decimal import Decimal

import pytest

from app import case_packages, table_import
from app.services import imports
from test_automatic_table_import import preview
from test_feedback_import_api import complete


def legacy_preview(client, cid, content, monkeypatch):
    return profile_preview(client, cid, content, monkeypatch, table_import.IDENTITY_PROFILE)


def profile_preview(client, cid, content, monkeypatch, profile):
    with monkeypatch.context() as context:
        context.setattr(imports, "TABLE_PROFILE", profile)
        return preview(client, cid, content)


@pytest.mark.parametrize("current_profile", ["funds-trace-table-v6", "funds-trace-table-v7"])
def test_ready_v5_repreview_and_commit_remain_frozen_after_role_profile_is_default(
        client, database, make_case, monkeypatch, current_profile):
    monkeypatch.setattr(imports, "TABLE_PROFILE", current_profile)
    cid = make_case("Synthetic old ready mapping")
    content = (b"date,payer_account,payee_account,account,counterparty account,direction,amount\n"
               b"2026-01-01,SYN-A,SYN-B,SYN-A,SYN-X,out,1.01\n")
    old = legacy_preview(client, cid, content, monkeypatch)
    assert old["table_summary"]["profile"] == table_import.IDENTITY_PROFILE
    assert old["stats"]["ok"] == 1
    rechecked = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview",
                            json={"mode": old["mode"], "mapping": old["mapping"]})
    assert rechecked.status_code == 200
    assert rechecked.json()["table_summary"]["profile"] == table_import.IDENTITY_PROFILE
    old_job = complete(client, cid, rechecked.json())
    assert old_job["stage"] == "done" and old_job["result"]["ok"] == 1
    before = database.q_raw("select * from flows where case_id=?", [cid])
    new = preview(client, cid, content)
    assert new["table_summary"]["profile"] == current_profile
    assert new["table_summary"]["unresolved"] == 1
    assert complete(client, cid, new)["stage"] == "done"
    assert database.q_raw("select * from flows where case_id=?", [cid]) == before
    assert client.get(f"/api/v1/cases/{cid}/imports/{old['import_id']}").json()["result"] == old_job["result"]
    coverage = client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]
    assert coverage["unresolved_records"] == 1
    assert not coverage["financial_interpretation_complete"]
    # A later frozen evaluation cannot overwrite the stronger v6 gap.
    assert complete(client, cid, legacy_preview(client, cid, content, monkeypatch))["stage"] == "done"
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"] == coverage


def test_v5_v6_v7_share_physical_keys_reuse_facts_and_round_trip_in_format4(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic compatible mapping package")
    content = b"date,payer_account,payee_account,amount\n2026-01-01,SYN-A,SYN-B,1.01\n"
    # The new XLS-only profile cannot reinterpret these historical CSV bytes.
    csv_profiles = table_import.SUPPORTED_PROFILES - {table_import.XLS_PROFILE}
    plans = [table_import.parse(io.BytesIO(content), suffix=".csv", profile=profile)
             for profile in sorted(csv_profiles)]
    assert len({plan["records"][0]["record_key"] for plan in plans}) == 1
    old = legacy_preview(client, cid, content, monkeypatch)
    assert complete(client, cid, old)["result"]["ok"] == 1
    for profile in sorted(table_import.ROLE_PROFILES - {table_import.XLS_PROFILE}):
        current = profile_preview(client, cid, content, monkeypatch, profile)
        job = complete(client, cid, current)
        assert job["stage"] == "done" and (job["result"]["ok"], job["result"]["rows_dup"]) == (0, 1)
    query = """select s.parser_version,r.record_key,r.disposition from import_records r
        join import_sources s on s.case_id=r.case_id and s.source_id=r.source_id
        where r.case_id=? order by s.parser_version"""
    before = database.q_raw(query, [cid])
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert database.q_raw("select amount from flows where case_id=?", [restored]) == [{"amount": Decimal("1.01")}]
    assert database.q_raw(query, [restored]) == before
    assert {record["parser_version"] for record in database.q_raw("select parser_version from import_sources where case_id=?", [restored])} == csv_profiles
    coverage = client.get(f"/api/v1/cases/{restored}/stats").json()["import_coverage"]
    assert coverage["unresolved_records"] == 0 and coverage["financial_interpretation_complete"]


@pytest.mark.parametrize("profile", ["funds-trace-table-v6", "funds-trace-table-v7"])
def test_composed_role_headers_follow_reordered_regions_with_full_evidence(profile):
    content = ("date,本方账户号码,对手方账户号码,direction,amount\n"
               "2026-01-01,SYN-A,SYN-B,out,1.01\n"
               "amount,direction,收款方账户号码,date,付款方账户号码\n"
               "2.02,in,SYN-D,2026-01-02,SYN-C\n").encode()
    plan = table_import.parse(content, suffix=".csv", profile=profile)
    assert plan["stats"]["ok"] == 2
    assert [record["reason_code"] for record in plan["records"]] == ["transaction_valid", "repeated_header", "transaction_valid"]
    assert [(row["payer_account"], row["payee_account"], row["amount"]) for row in plan["rows"]] == [
        ("SYN-A", "SYN-B", Decimal("1.01")), ("SYN-C", "SYN-D", Decimal("2.02"))]
    assert {entry["header_row"] for entry in plan["mapping_evidence"]} == {1, 3}
    assert any(entry["basis"] == "role_qualified_header" for entry in plan["mapping_evidence"])


@pytest.mark.parametrize("profile", ["funds-trace-table-v6", "funds-trace-table-v7"])
def test_incomplete_conflicting_roles_remain_readable_after_package_restore(client, database, make_case, monkeypatch, profile):
    cid = make_case("Synthetic conflicting role package")
    content = (b"date,payer account,payee account,account,counterparty account,direction,amount\n"
               b"2026-01-01,SYN-WRONG,,SYN-OWN,SYN-OTHER,out,1.23\n")
    view = profile_preview(client, cid, content, monkeypatch, profile)
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["table_summary"]["unresolved"] == 1
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    restored_source = database.q_raw("select source_id from import_sources where case_id=?", [restored])[0]["source_id"]
    records = client.get(f"/api/v1/cases/{restored}/sources/{restored_source}/report").json()["items"]
    assert records[0]["reason_code"] == "conflicting_field"
    assert records[0]["details"]["validation_issues"].split(",")[0] == "conflicting_field"
    assert records[0]["flow_id"] is None
    assert database.q_raw("select amount from flows where case_id=?", [restored]) == []
