"""Public discovery, source access and retention use one explicit case scope."""
from datetime import datetime
import hashlib
import time

import pytest

from app import importer
from app.services import imports


CSV = ("合成报表\n\n交易时间,付款账号,收款账号,交易金额\n"
       "2026-01-01 10:00:00,000SYN-A,000SYN-B,123.45\n\n"
       "2026-01-01 10:00:00,000SYN-A,000SYN-B,123.45\n"
       "invalid,000SYN-A,000SYN-B,5.00\n").encode()


def complete(client, cid, preview):
    base = f"/api/v1/cases/{cid}/imports/{preview['import_id']}"
    assert client.post(base + "/commit", json={"mode": preview["mode"], "mapping": preview["mapping"]}).status_code == 200
    for _ in range(400):
        response = client.get(base)
        assert response.status_code == 200, response.text
        job = response.json()
        if job["stage"] in ("done", "error"):
            assert job["stage"] == "done", job
            return job
        time.sleep(.01)
    raise AssertionError("Synthetic import timed out")


def upload(client, cid):
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("synthetic.csv", CSV)})
    assert response.status_code == 200, response.text
    return response.json()


def test_nonempty_session_discovery_and_paginated_sources_are_case_bound(client, database, make_case):
    cid, other = make_case("Source API"), make_case("Other source API")
    preview = upload(client, cid)
    sessions = client.get(f"/api/v1/cases/{cid}/imports")
    assert sessions.status_code == 200, sessions.text
    item = sessions.json()["items"][0]
    assert item["import_id"] == preview["import_id"] and item["stage"] == "ready"
    assert item["created_at"].endswith("Z") and item["expires_at"].endswith("Z")
    assert "owner_token" not in item and "suffix" not in item and "columns_json" not in item
    assert client.get(f"/api/v1/cases/{other}/imports").json()["items"] == []
    assert preview["stats"]["bad_examples"][0]["source_row"] == 7
    complete(client, cid, preview)
    fid = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]["id"]
    base = f"/api/v1/cases/{cid}/flows/{fid}/sources"
    pages = [client.get(base, params={"page": page, "page_size": 1}) for page in (1, 2)]
    assert all(response.status_code == 200 for response in pages)
    records = [response.json()["items"][0] for response in pages]
    # Flow links use the journal sequence; member_row remains the physical CSV
    # position, including preamble/blank records excluded from the flow table.
    assert [record["source_row"] for record in records] == [3, 5]
    assert [record["member_row"] for record in records] == [4, 6]
    journal = client.get(f"/api/v1/cases/{cid}/imports/{preview['import_id']}/records").json()
    assert journal["total"] == 6
    assert [(record["record_no"], record["source_row"]) for record in journal["items"]] == [
        (1, 1), (2, 2), (3, 4), (4, 5), (5, 6), (6, 7)]
    assert journal["items"][-1]["disposition"] == "unresolved"
    assert journal["items"][-1]["reason_code"] == "invalid_time"
    assert {record["disposition"] for record in records} == {"imported", "duplicate"}
    for record in records:
        assert record["sha256"] == hashlib.sha256(CSV).hexdigest()
        assert record["source_available"] and record["row_kind"] == "table_record" and record["sheet"] == ""
        assert record["member_path"] == "table.csv"
        matched = journal["items"][record["source_row"] - 1]
        assert matched["member_path"] == record["member_path"] and matched["source_row"] == record["member_row"]
        assert matched["flow_id"] == fid and matched["disposition"] == record["disposition"]
        assert matched["details"] == record["record_details"]
        elapsed = datetime.fromisoformat(record["imported_at"]) - datetime.fromisoformat(item["created_at"])
        assert 0 <= elapsed.total_seconds() < 60
    assert pages[0].json()["total"] == 2
    assert client.get(base, params={"page_size": 201}).status_code == 422
    assert client.get(f"/api/v1/cases/{other}/flows/{fid}/sources").status_code == 404
    source_url = f"/api/v1/cases/{cid}/sources/{preview['import_id']}/file"
    original = client.get(source_url)
    assert original.status_code == 200 and original.content == CSV
    assert original.headers["content-type"] == "application/octet-stream"
    assert original.headers["cache-control"] == "no-store"
    assert client.get(f"/api/v1/cases/{other}/sources/{preview['import_id']}/file").status_code == 404


@pytest.mark.parametrize("legacy_without_journal", [False, True])
def test_package_sources_remain_metadata_and_cleanup_respects_retained_evidence(
        client, database, make_case, monkeypatch, legacy_without_journal):
    cid = make_case("Source lifecycle")
    with monkeypatch.context() as profile:
        if legacy_without_journal:
            profile.setattr(imports, "TABLE_PROFILE", importer.PARSER_VERSION)
        preview = upload(client, cid)
    job = complete(client, cid, preview)
    package = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    restored_id = restored.json()["case_id"]
    fid = client.get(f"/api/v1/cases/{restored_id}/flows").json()["items"][0]["id"]
    origins = client.get(f"/api/v1/cases/{restored_id}/flows/{fid}/sources").json()["items"]
    assert len(origins) == 2
    assert all(item["origin"] == "package" and not item["source_available"] for item in origins)
    assert all(item["source_id"] != preview["import_id"] for item in origins)
    assert client.get(f"/api/v1/cases/{restored_id}/sources/{origins[0]['source_id']}/file").status_code == 404
    database.x("update import_sessions set expires_at=current_timestamp-interval '1 day' where import_id=?", [preview["import_id"]])
    cleanup = f"/api/v1/cases/{cid}/imports/cleanup"
    protected = client.post(cleanup, json={"dry_run": False})
    assert protected.status_code == 200 and protected.json()["protected"] == 1
    path = database.DATA_DIR / "uploads" / f"{preview['import_id']}.csv"
    assert path.read_bytes() == CSV
    assert client.delete(f"/api/batches/{job['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    plan = client.post(cleanup, json={})
    assert plan.status_code == 200 and plan.json()["dry_run"]
    assert path.is_file()
    applied = client.post(cleanup, json={"dry_run": False})
    assert applied.status_code == 200
    if legacy_without_journal:
        assert plan.json()["eligible"] == 1 and plan.json()["protected"] == 0
        assert applied.json()["deleted"] == 1 and applied.json()["protected"] == 0 and not path.exists()
        assert database.q("select count(*) n from import_records where source_id=?", [preview["import_id"]]) == [{"n": 0}]
    else:
        assert plan.json()["eligible"] == 0 and plan.json()["protected"] == 1
        assert applied.json()["deleted"] == 0 and applied.json()["protected"] == 1 and path.read_bytes() == CSV
        assert database.q("select record_no,source_row,reason_code from import_records where source_id=? and disposition='removed' order by record_no",
                          [preview["import_id"]]) == [
            {"record_no": 3, "source_row": 4, "reason_code": "flow_deleted"},
            {"record_no": 5, "source_row": 6, "reason_code": "flow_deleted"}]
        assert database.q("select record_no,source_row,reason_code from import_records where source_id=? and disposition='unresolved'",
                          [preview["import_id"]]) == [{"record_no": 6, "source_row": 7, "reason_code": "invalid_time"}]
        assert database.q("select count(*) n from import_records where source_id=?", [preview["import_id"]]) == [{"n": 6}]
        original = client.get(f"/api/v1/cases/{cid}/sources/{preview['import_id']}/file")
        assert original.status_code == 200 and original.content == CSV
    receipt = client.get(f"/api/v1/cases/{cid}/imports/{preview['import_id']}").json()
    assert receipt["stage"] == "done" and receipt["result"] == job["result"]
    assert receipt["source_available"] is (not legacy_without_journal)
    assert client.get(f"/api/v1/cases/{restored_id}/flows").json()["total"] == 1


def test_additive_import_schema_preserves_an_existing_synthetic_database(database, make_case, flow_row):
    from app import importer
    cid = make_case("Before import catalog")
    importer.commit_rows("legacy-synthetic.csv", "A", [flow_row()], case_id=cid)
    with database._lock:
        for table in ("flow_sources", "import_sources", "import_sessions", "import_file_gc"):
            database.con().execute(f"drop table {table}")
        database._con.close()
        database._con = None
        database.con()
    assert database.q("select name from cases where id=?", [cid]) == [{"name": "Before import catalog"}]
    assert database.q_raw("select cast(amount as varchar) amount from flows where case_id=?", [cid]) == [{"amount": "123.45"}]
    for table in ("flow_sources", "import_sources", "import_sessions", "import_file_gc"):
        assert database.q(f"select count(*) n from {table}") == [{"n": 0}]


def test_failed_initialization_does_not_publish_a_partially_initialized_connection(database, make_case, monkeypatch):
    import pytest

    cid = make_case("Synthetic initialization failure")
    database._con.close()
    database._con = None
    migrate = database._migrate

    def fail(connection):
        connection.execute("create table synthetic_uncommitted_schema(value integer)")
        raise RuntimeError("Synthetic initialization failure")

    monkeypatch.setattr(database, "_migrate", fail)
    with pytest.raises(RuntimeError, match="Synthetic initialization failure"):
        database.con()
    assert database._con is None
    monkeypatch.setattr(database, "_migrate", migrate)
    assert database.q("select name from cases where id=?", [cid]) == [{"name": "Synthetic initialization failure"}]
    assert not database.q("select table_name from information_schema.tables where table_name='synthetic_uncommitted_schema'")
