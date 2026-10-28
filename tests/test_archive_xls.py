"""Versioned directory XLS evidence, using real synthetic BIFF8 workbooks."""
from decimal import Decimal
import io

import pytest
import xlwt

from app import event_resolution, table_archive, table_import
from test_event_import import transaction, csv_rows
from test_folder_evidence import archive, preview, complete, table
from test_table_xls_reader import formula_workbook
from test_table_write_integrity import persisted
from xls_fixtures import Cell, Error, workbook


V7 = "funds-trace-table-archive-v7"


def payload(rows):
    headers = list(rows[0])
    return workbook([("Synthetic own ledger", [headers, *[[row.get(key, "") for key in headers] for row in rows]])])


def test_directory_xls_public_repreview_commit_and_package_keep_source_evidence(client, database, make_case):
    cid = make_case("Synthetic directory XLS")
    data = payload([transaction("SYN-A"), transaction("SYN-B")])
    view = preview(client, cid, [("folder/synthetic.xls", data)], scope_bank=None)
    assert view["table_summary"]["profile"] == V7
    assert (view["table_summary"]["accepted"], view["table_summary"]["unresolved"]) == (2, 0)
    refreshed = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview",
                           json={"mode": view["mode"], "mapping": view["mapping"]})
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["table_summary"] == view["table_summary"]
    job = complete(client, cid, refreshed.json())
    assert job["stage"] == "done" and job["result"]["ok"] == 2, job
    assert event_resolution.summary(cid)["confirmed_events"] == 1
    facts = database.q_raw("select amount,balance from flows where case_id=? order by balance", [cid])
    assert facts == [{"amount": Decimal("100.00"), "balance": Decimal("50.00")},
                     {"amount": Decimal("100.00"), "balance": Decimal("200.00")}]
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    assert [item["source_row"] for item in records] == [2, 3]
    assert all(item["member_path"].startswith("folder/synthetic.xls/worksheets/") for item in records)
    assert all(event_resolution.DETAIL_FIELD in item["details"] for item in records)
    package = client.get(f"/api/cases/{cid}/export")
    assert package.status_code == 200, package.text
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    restored_id = restored.json()["case_id"]
    assert event_resolution.summary(restored_id)["confirmed_events"] == 1
    assert database.q_raw("select amount,balance from flows where case_id=? order by balance", [restored_id]) == facts
    assert database.q_raw("select distinct parser_version from import_sources where case_id=?", [restored_id]) == [{"parser_version": V7}]
    assert client.get(f"/api/v1/cases/{restored_id}/stats").json()["import_coverage"]["financial_interpretation_complete"]
    replay = complete(client, cid, preview(client, cid, [("folder/synthetic.xls", data)], scope_bank=None))
    assert replay["stage"] == "done" and (replay["result"]["ok"], replay["result"]["rows_dup"]) == (0, 2)


def commit_directory(client, cid, data):
    view = preview(client, cid, [("synthetic.xls", data)], scope_bank=None)
    assert view["table_summary"]["profile"] == V7
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    return view, job, records


@pytest.mark.parametrize("zipped", [False, True])
def test_nested_zip_xls_uses_v7_and_retains_all_member_positions(client, database, make_case, zipped):
    cid = make_case("Synthetic nested XLS")
    data = payload([transaction("SYN-A"), transaction("SYN-B")])
    members = [("bundle.zip", archive([("nested.zip", archive([("ledger.xls", data)]))]))]
    view = preview(client, cid, members, zipped=zipped, scope_bank=None)
    assert view["table_summary"]["profile"] == V7
    assert (view["table_summary"]["accepted"], view["table_summary"]["unresolved"]) == (2, 0)
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 2, job
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    assert [item["source_row"] for item in records] == [2, 3]
    assert all(item["member_path"].startswith("bundle.zip/nested.zip/ledger.xls/worksheets/") for item in records)
    assert event_resolution.summary(cid)["confirmed_events"] == 1
    assert database.q_raw("select distinct parser_version from import_sources where case_id=?", [cid]) == [{"parser_version": V7}]


def test_new_flat_zip_xls_uses_v7_but_plain_csv_zip_keeps_v4(client, make_case):
    cid = make_case("Synthetic flat XLS ZIP")
    view = preview(client, cid, [("ledger.xls", payload([transaction("SYN-A"), transaction("SYN-B")]))],
                   zipped=True, scope_bank=None)
    assert view["table_summary"]["profile"] == V7
    assert complete(client, cid, view)["result"]["ok"] == 2
    assert event_resolution.summary(cid)["confirmed_events"] == 1
    ordinary = make_case("Synthetic unchanged ordinary ZIP")
    plain = csv_rows([{"date": "2026-01-01", "payer_account": "SYN-A", "payee_account": "SYN-B", "amount": "12.34"}])
    view = preview(client, ordinary, [("ledger.csv", plain)], zipped=True, scope_bank=None)
    assert view["table_summary"]["profile"] == table_archive.PROFILE == "funds-trace-table-archive-v4"
    assert complete(client, ordinary, view)["result"]["ok"] == 1
    assert event_resolution.summary(ordinary) is None


@pytest.mark.parametrize("zipped", [False, True])
def test_nested_csv_without_xls_keeps_existing_profile(client, make_case, zipped):
    cid = make_case("Synthetic nested CSV compatibility")
    plain = csv_rows([{"date": "2026-01-01", "payer_account": "SYN-A", "payee_account": "SYN-B", "amount": "12.34"}])
    view = preview(client, cid, [("bundle.zip", archive([("nested.zip", archive([("ledger.csv", plain)]))]))],
                   zipped=zipped, scope_bank=None)
    assert view["table_summary"]["profile"] == (table_archive.PROFILE if zipped else table_archive.EVIDENCE_PROFILE)
    assert complete(client, cid, view)["result"]["ok"] == 1


@pytest.mark.parametrize("contains_xls", [False, True])
def test_profile_selection_only_opens_nested_containers(monkeypatch, contains_xls):
    import zipfile
    original = zipfile.ZipFile.open
    opened = []
    def containers_only(archive_file, member, *args, **kwargs):
        name = member.filename if isinstance(member, zipfile.ZipInfo) else member
        assert name.endswith(".zip"), "Profile detection must not parse transaction or XLS contents"
        opened.append(name)
        return original(archive_file, member, *args, **kwargs)
    leaf = "ledger.xls" if contains_xls else "ledger.csv"
    data = archive([("outer.csv", b"not read"), ("bundle.zip", archive([("nested.zip", archive([(leaf, b"not read")]))]))])
    monkeypatch.setattr(zipfile.ZipFile, "open", containers_only)
    stream = io.BytesIO(data)
    assert table_archive.evidence_profile(stream) == (V7 if contains_xls else table_archive.EVIDENCE_PROFILE)
    assert opened == ["bundle.zip", "nested.zip"]
    assert stream.tell() == 0


@pytest.mark.parametrize("limit", ["depth", "member_size"])
def test_nested_xls_keeps_existing_archive_resource_limits(client, database, make_case, monkeypatch, limit):
    cid = make_case("Synthetic nested XLS limits")
    data = payload([transaction("SYN-A")])
    nested = archive([("ledger.xls", data)])
    if limit == "depth":
        nested = archive([("one.zip", archive([("two.zip", nested)]))])
    else:
        monkeypatch.setattr(table_archive, "MAX_MEMBER_BYTES", len(data) - 1)
    before = persisted(database)
    response = client.post(f"/api/v1/cases/{cid}/imports/directory/preview",
                           files=[("files", ("bundle.zip", nested, "application/zip"))])
    assert response.status_code == 400, response.text
    assert persisted(database) == before
    assert database.q_raw("select * from import_sessions where case_id=?", [cid]) == []
    assert not list((database.DATA_DIR / "uploads").glob("*"))


def test_ready_v6_retains_xls_attachment_and_single_xls_remains_v18(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic pinned archive v6")
    data = payload([transaction("SYN-B")])
    with monkeypatch.context() as historical:
        historical.setattr(table_archive, "evidence_profile", lambda source: table_archive.EVIDENCE_PROFILE)
        view = preview(client, cid, [("synthetic.xls", data), ("neighbor.csv", csv_rows([transaction("SYN-A")]))], scope_bank=None)
    assert view["table_summary"]["profile"] == "funds-trace-table-archive-v6"
    assert (view["table_summary"]["accepted"], view["table_summary"]["unresolved"]) == (1, 1)
    refreshed = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview",
                           json={"mode": view["mode"], "mapping": view["mapping"]})
    assert refreshed.status_code == 200 and refreshed.json()["table_summary"] == view["table_summary"]
    job = complete(client, cid, refreshed.json())
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    attachment = database.q_raw("select role,encoding from import_members where case_id=? and member_path='synthetic.xls'", [cid])
    assert attachment == [{"role": "attachment", "encoding": "binary"}]
    single = make_case("Synthetic unchanged single XLS")
    response = client.post(f"/api/v1/cases/{single}/imports/preview", files={"file": ("synthetic.xls", data)})
    assert response.status_code == 200, response.text
    assert response.json()["table_summary"]["profile"] == table_import.XLS_PROFILE == "funds-trace-table-v18"
    assert complete(client, single, response.json())["stage"] == "done"
    assert event_resolution.summary(single) is None
    with pytest.raises(table_import.TableImportError):
        table_import.parse(io.BytesIO(data), suffix=".xls", profile=table_archive.EVIDENCE_PROFILE,
                           reference_policy=table_import.TRANSACTION_REFERENCE_POLICY)


def test_ready_v4_nested_zip_keeps_its_pinned_interpretation(client, make_case, monkeypatch):
    cid = make_case("Synthetic pinned archive v4")
    plain = csv_rows([{"date": "2026-01-01", "payer_account": "SYN-A", "payee_account": "SYN-B", "amount": "12.34"}])
    with monkeypatch.context() as historical:
        historical.setattr(table_archive, "evidence_profile", lambda source: table_archive.EVIDENCE_PROFILE)
        view = preview(client, cid, [("nested.zip", archive([("ledger.csv", plain)]))], zipped=True, scope_bank=None)
    assert view["table_summary"]["profile"] == table_archive.PROFILE
    refreshed = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview",
                           json={"mode": view["mode"], "mapping": view["mapping"]})
    assert refreshed.status_code == 200 and refreshed.json()["table_summary"] == view["table_summary"]
    assert complete(client, cid, refreshed.json())["result"]["ok"] == 1
    assert event_resolution.summary(cid) is None


@pytest.mark.parametrize("bad,reason", [
    (True, "invalid_cell_type"), (Error(), "invalid_cell_type"),
    (Cell(60, "yyyy-mm-dd"), "invalid_cell_type"), (xlwt.Formula("1+2"), "formula_value"),
])
def test_v7_critical_formula_and_invalid_cells_never_enter_flows(client, database, make_case, bad, reason):
    cid = make_case("Synthetic XLS typed rejection")
    valid = transaction("SYN-A")
    invalid = transaction("SYN-B", **{"交易金额": bad})
    data = payload([valid, invalid])
    if reason == "formula_value":
        data = formula_workbook(data, cache_kind="number", cached=100.0)
    view, job, records = commit_directory(client, cid, data)
    assert (job["result"]["ok"], view["table_summary"]["unresolved"]) == (1, 1)
    assert [(r["disposition"], r["reason_code"]) for r in records] == [("imported", "transaction_valid"), ("unresolved", reason)]
    assert records[1]["flow_id"] is None
    assert event_resolution.LEDGER_FIELD not in records[1]["details"]
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid]) == [{"n": 1}]
    package = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/v1/cases/{restored.json()['case_id']}/stats").json()["import_coverage"]["unresolved_records"] == 1


@pytest.mark.parametrize("field", ["交易金额", "记账顺序号", "本笔交易后账面余额"])
def test_cached_formula_header_cannot_declare_financial_or_ledger_schema(client, make_case, field):
    cid = make_case("Synthetic cached XLS header")
    row = transaction("SYN-B")
    headers = list(row)
    headers[headers.index(field)] = xlwt.Formula('"' + field + '"')
    raw = workbook([("Synthetic header cache", [headers, list(row.values())])])
    data = formula_workbook(raw, cache_kind="string", cached=field)
    _, job, records = commit_directory(client, cid, data)
    if field == "交易金额":
        assert job["result"]["ok"] == 0
    else:
        assert job["result"]["ok"] == 1
        ledger = records[0]["details"].get(event_resolution.LEDGER_FIELD, "")
        if field == "记账顺序号":
            assert not ledger or '"posting_order":""' in ledger
        else:
            assert not ledger or '"balance_kind":"unknown"' in ledger


def test_v7_formula_auxiliary_account_does_not_create_identity_evidence(client, make_case):
    from app import evidence_resolution
    cid = make_case("Synthetic auxiliary XLS cache")
    data = workbook([("Synthetic account evidence", [
        ["交易账号", "交易卡号", "账号开户银行", "账户开户名称"],
        [xlwt.Formula('"SYN-A"'), "SYN-CARD", "Synthetic bank", "Synthetic owner"]])])
    data = formula_workbook(data, cache_kind="string", cached="SYN-A")
    _, job, records = commit_directory(client, cid, data)
    assert job["result"]["ok"] == 0
    assert records[0]["disposition"] == "unresolved" and records[0]["reason_code"] == "formula_value"
    assert evidence_resolution.DETAIL_FIELD not in records[0]["details"]


@pytest.mark.parametrize("formula", [False, True])
def test_v7_closed_feedback_adapter_keeps_xls_typed_rejection(client, database, make_case, formula):
    from app import feedback_import
    from test_folder_evidence import transaction as feedback_transaction
    cid = make_case("Synthetic XLS feedback adapter")
    row = feedback_transaction()
    if formula:
        row["交易金额"] = xlwt.Formula("1+2")
    data = payload([{column: row.get(column, "") for column in feedback_import.COLUMNS}])
    if formula:
        data = formula_workbook(data, cache_kind="number", cached=10.25)
    _, job, records = commit_directory(client, cid, data)
    assert job["result"]["ok"] == (0 if formula else 1)
    assert records[0]["reason_code"] == ("formula_value" if formula else "transaction_valid")
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid]) == [{"n": 0 if formula else 1}]


@pytest.mark.parametrize("data", [b"not an XLS workbook", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
                                table("account", [{"交易账号": "SYN-A"}])],
                         ids=["text", "truncated_ole", "auxiliary_csv"])
def test_malformed_xls_rolls_back_entire_directory_before_writes(client, database, make_case, data):
    cid = make_case("Synthetic broken XLS")
    before = persisted(database)
    response = client.post(f"/api/v1/cases/{cid}/imports/directory/preview", files=[
        ("files", ("a-valid.csv", csv_rows([transaction("SYN-A")]), "application/octet-stream")),
        ("files", ("z-broken.xls", data, "application/octet-stream"))])
    assert response.status_code == 400, response.text
    assert persisted(database) == before
    assert database.q_raw("select * from import_sessions where case_id=?", [cid]) == []
    assert not list((database.DATA_DIR / "uploads").glob("*"))


def test_v7_package_cannot_mislabel_xls_as_v6(client, make_case, tmp_path):
    from test_feedback_package import _archive, _members, _mutate
    cid = make_case("Synthetic XLS package profile")
    commit_directory(client, cid, payload([transaction("SYN-B")]))
    package = client.get(f"/api/cases/{cid}/export")
    members = _members(package.content)
    _mutate(members, "import_sources", "update entries set parser_version='funds-trace-table-archive-v6'", tmp_path)
    response = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", _archive(members))})
    assert response.status_code == 400, response.text
