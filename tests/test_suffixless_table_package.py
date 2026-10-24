"""Public package round trips for text sources that already allow an empty suffix."""
import csv
from decimal import Decimal
import hashlib
import io

import pytest

from app import case_packages, table_import
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_feedback_package import _archive, _journal, _members, _mutate
from test_table_contract_lifecycle import facts
from test_table_source_roles import ACCOUNT


def text_source(encoding="utf-8-sig", *, auxiliary=False):
    auxiliary_row = {"账号": "SYN-OWN", "账户余额": "100.00", "可用余额": "100.00",
                     "币种": "CNY", "账户状态": "正常", "备注": "合成账户资料"}
    rows = ([ACCOUNT, [auxiliary_row.get(header, "") for header in ACCOUNT]]
            if auxiliary else
            [["date", "payer_account", "payee_account", "amount", "currency", "summary"],
             ["2026-01-01 10:00:00", "SYN-A", "SYN-B", "1.23", "USD", "合成交易"]])
    output = io.StringIO(newline="")
    csv.writer(output).writerows(rows)
    return output.getvalue().encode(encoding)


def import_source(client, case_id, data, filename):
    view = preview(client, case_id, data, filename)
    job = complete(client, case_id, view)
    assert job["stage"] == "done", job
    return view, job


def exported(client, case_id):
    response = client.get(f"/api/cases/{case_id}/export")
    assert response.status_code == 200, response.text
    return response.content


def restored(client, content):
    return client.post("/api/cases/import", files={"file": ("synthetic.fundcase", content)})


@pytest.mark.parametrize("filename", ["ordinary", ".hidden", "ordinary."])
@pytest.mark.parametrize("encoding", ["utf-8-sig", "gb18030", "utf-16"])
@pytest.mark.parametrize("auxiliary", [False, True], ids=["transaction", "auxiliary"])
def test_suffixless_text_public_import_export_and_restore_preserves_facts(
        client, database, make_case, filename, encoding, auxiliary):
    cid = make_case("Synthetic empty-suffix package")
    raw = text_source(encoding, auxiliary=auxiliary)
    view, job = import_source(client, cid, raw, filename)
    assert job["result"]["ok"] == (0 if auxiliary else 1)
    assert database.q_raw("select amount,currency from flows where case_id=?", [cid]) == (
        [] if auxiliary else [{"amount": Decimal("1.23"), "currency": "USD"}])
    assert database.q_raw("select role,encoding from import_members where case_id=?", [cid]) == [{
        "role": "auxiliary" if auxiliary else "table", "encoding": encoding}]
    original = facts(database, cid)
    response = restored(client, exported(client, cid))
    assert response.status_code == 200, response.text
    new_case = response.json()["case_id"]
    assert facts(database, new_case) == original
    source = database.q_raw("""select filename,sha256,size_bytes,sheet,parser_version,row_kind
        from import_sources where case_id=?""", [new_case])[0]
    assert source == {"filename": filename, "sha256": hashlib.sha256(raw).hexdigest(),
                      "size_bytes": len(raw), "sheet": "", "parser_version": table_import.PROFILE,
                      "row_kind": "table_record"}
    for table in case_packages.JOURNAL_PKG_COLS:
        assert _journal(database, table, new_case) == _journal(database, table, cid)
    assert view["filename"] == filename


def persistent_snapshot(database):
    tables = ("cases", *case_packages.CASE_PKG_TABLES,
              *case_packages.PROVENANCE_PKG_COLS, *case_packages.JOURNAL_PKG_COLS)
    return {table: database.q_raw(f"select * from {table} order by all") for table in tables}


def assert_atomic_rejection(client, database, members):
    before = persistent_snapshot(database)
    photos = database.DATA_DIR / "photos"
    before_files = {path.relative_to(photos) for path in photos.rglob("*")}
    response = restored(client, _archive(members))
    assert response.status_code == 400, response.text
    assert persistent_snapshot(database) == before
    assert {path.relative_to(photos) for path in photos.rglob("*")} == before_files


@pytest.mark.parametrize("defect", [
    "source_sha", "source_size", "member_sha", "member_size", "member_path",
    "member_encoding", "member_xls_encoding", "member_unknown_encoding", "member_role", "sheet", "missing_members", "missing_records",
    "missing_both_journals", "empty_members", "multiple_members", "empty_records",
    "record_key", "changed_flow", "future_profile", "legacy_kind",
])
def test_suffixless_package_requires_a_closed_single_text_member_and_complete_journal(
        client, database, make_case, tmp_path, defect):
    cid = make_case("Synthetic invalid empty-suffix source")
    import_source(client, cid, text_source(), "ordinary")
    members = _members(exported(client, cid))
    mutations = {
        "source_sha": ("import_sources", "update entries set sha256='" + "a" * 64 + "'"),
        "source_size": ("import_sources", "update entries set size_bytes=size_bytes+1"),
        "member_sha": ("import_members", "update entries set sha256='" + "b" * 64 + "'"),
        "member_size": ("import_members", "update entries set size_bytes=size_bytes+1"),
        "member_path": ("import_members", "update entries set member_path='other.csv'"),
        "member_encoding": ("import_members", "update entries set encoding='xlsx'"),
        "member_xls_encoding": ("import_members", "update entries set encoding='xls'"),
        "member_unknown_encoding": ("import_members", "update entries set encoding='unknown'"),
        "member_role": ("import_members", "update entries set role='transaction'"),
        "sheet": ("import_sources", "update entries set sheet='workbook'"),
        "empty_members": ("import_members", "delete from entries"),
        "multiple_members": ("import_members", """insert into entries
            select source_id,'second.csv',role,sha256,size_bytes,encoding,row_count from entries"""),
        "empty_records": ("import_records", "delete from entries"),
        "record_key": ("import_records", "update entries set record_key='" + "0" * 64 + "'"),
        "changed_flow": ("flows", "update entries set amount=amount+1"),
        "future_profile": ("import_sources", "update entries set parser_version='funds-trace-table-v99'"),
        "legacy_kind": ("import_sources", "update entries set row_kind='csv_record',parser_version='legacy-csv'"),
    }
    if defect.startswith("missing_"):
        names = ({"missing_members": ["import_members"], "missing_records": ["import_records"],
                  "missing_both_journals": ["import_members", "import_records"]})[defect]
        for name in names:
            del members[name + ".parquet"]
    else:
        table, sql = mutations[defect]
        _mutate(members, table, sql, tmp_path)
    assert_atomic_rejection(client, database, members)


@pytest.mark.parametrize("kind", ["xlsx", "xls", "zip", "zip-as-ordinary", "flat-zip-as-ordinary"])
def test_renamed_excel_or_archive_cannot_enter_suffixless_text_package_branch(
        client, database, make_case, tmp_path, kind):
    rows = [["date", "payer_account", "payee_account", "amount", "currency"],
            ["2026-01-01", "SYN-A", "SYN-B", "1.23", "USD"]]
    if kind == "xlsx":
        from test_table_multisheet_append import encoded
        data, filename = encoded([("Synthetic Excel", rows)], ".xlsx"), "synthetic.xlsx"
    elif kind == "xls":
        from xls_fixtures import workbook
        data, filename = workbook([("Synthetic XLS", rows)]), "synthetic.xls"
    elif kind == "flat-zip-as-ordinary":
        from test_table_cancellations import source, transaction
        data, filename = source([transaction()], path="table.csv")[1], "synthetic.zip"
    else:
        from test_table_container_identity import archive_bytes
        data = archive_bytes([("bank/source.csv", text_source())], nested=True)
        filename = "synthetic.zip"
    cid = make_case("Synthetic disguised package source")
    _, job = import_source(client, cid, data, filename)
    assert job["result"]["ok"] == 1
    package = exported(client, cid)
    # Positive control: these existing explicit formats still restore unchanged.
    response = restored(client, package)
    assert response.status_code == 200, response.text
    assert facts(database, response.json()["case_id"]) == facts(database, cid)
    members = _members(package)
    sql = "update entries set filename='ordinary',sheet=''"
    if kind in ("zip-as-ordinary", "flat-zip-as-ordinary"):
        sql += ",parser_version='" + table_import.PROFILE + "'"
    _mutate(members, "import_sources", sql, tmp_path)
    assert_atomic_rejection(client, database, members)


@pytest.mark.parametrize("profile", ["funds-trace-table-v5", "funds-trace-table-v17"])
def test_existing_explicit_xlsx_historical_labels_remain_compatible(
        client, database, make_case, flow_row, tmp_path, profile):
    from test_table_package import _seed_table
    if profile == "funds-trace-table-v5":
        cid, _, _ = _seed_table(database, make_case, flow_row, profile=profile,
                               filename="synthetic.xlsx", sheet="workbook")
        package = exported(client, cid)
    else:
        cid = make_case("Synthetic historical worksheet label")
        import_source(client, cid, text_source(), "synthetic.csv")
        members = _members(exported(client, cid))
        # Existing packages may use worksheet labels with historical CSV-style
        # members. The explicit-XLSX acceptance branch must remain unchanged.
        _mutate(members, "import_sources",
                "update entries set filename='synthetic.xlsx',sheet='workbook'", tmp_path)
        package = _archive(members)
    response = restored(client, package)
    assert response.status_code == 200, response.text
    assert facts(database, response.json()["case_id"]) == facts(database, cid)
