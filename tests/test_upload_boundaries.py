"""Public upload budgets use tiny synthetic inputs, never large allocations."""
import io
from pathlib import Path

import pandas as pd
import pytest
from PIL import Image
from starlette.datastructures import UploadFile

from app import case_packages, importer, main
from test_cases import exported, populated_case, import_package


def identities_file(rows=1, extension="csv"):
    frame = pd.DataFrame({"卡号": [f"SYN-{i}" for i in range(rows)],
                          "身份证号": ["123456789012345"] * rows})
    if extension == "xlsx":
        output = io.BytesIO()
        frame.to_excel(output, index=False)
        return output.getvalue()
    return frame.to_csv(index=False).encode()


def upload_ids(client, cid, content, extension="csv"):
    return client.post("/api/account-ids/import", data={"case_id": cid},
                       files={"file": (f"synthetic.{extension}", content)})


def photo_bytes():
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(output, "PNG")
    return output.getvalue()


def upload_photo(client, cid, content):
    return client.post("/api/account-photos/set", data={"case_id": cid, "account": "SYN"},
                       files={"file": ("synthetic.png", content, "image/png")})


def assert_no_temporary_uploads(database):
    directory = database.DATA_DIR / "uploads"
    assert not directory.exists() or not list(directory.iterdir())


def test_case_upload_passes_seekable_spool_without_materializing_request(
        client, database, make_case, flow_row, monkeypatch):
    content = exported(client, populated_case(database, make_case, flow_row))
    async def forbid_materialization(self, size=-1):
        raise AssertionError("case packages must consume the seekable spool")
    monkeypatch.setattr(UploadFile, "read", forbid_materialization)
    response = import_package(client, content)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("limit", ["MAX_PACKAGE_BYTES", "MAX_PACKAGE_EXPANDED_BYTES",
                                 "MAX_PACKAGE_MEMBER_BYTES", "MAX_PACKAGE_MEMBERS",
                                 "MAX_PACKAGE_META_BYTES", "MAX_PACKAGE_ROWS"])
def test_case_package_budget_rejects_before_case_or_photo_publication(
        client, database, make_case, flow_row, monkeypatch, limit):
    content = exported(client, populated_case(database, make_case, flow_row))
    before = database.q("select * from cases order by id")
    photo_root = database.DATA_DIR / "photos"
    files_before = {p.relative_to(photo_root): p.read_bytes() for p in photo_root.rglob("*") if p.is_file()}
    temporary_paths = []
    original_temporary_directory = case_packages.tempfile.TemporaryDirectory
    def record_temporary_directory(*args, **kwargs):
        directory = original_temporary_directory(*args, **kwargs)
        temporary_paths.append(directory.name)
        return directory
    monkeypatch.setattr(case_packages.tempfile, "TemporaryDirectory", record_temporary_directory)
    monkeypatch.setattr(case_packages, limit, 1, raising=False)
    response = import_package(client, content)
    assert response.status_code == 400, response.text
    assert database.q("select * from cases order by id") == before
    assert {p.relative_to(photo_root): p.read_bytes() for p in photo_root.rglob("*") if p.is_file()} == files_before
    assert not list(photo_root.glob(".fundcase-*"))
    assert temporary_paths and all(not Path(path).exists() for path in temporary_paths)


@pytest.mark.parametrize("budget,extension", [("ID_TABLE_MAX_BYTES", "csv"),
                                             ("ID_TABLE_MAX_ROWS", "csv"),
                                             ("ID_TABLE_MAX_COLUMNS", "csv"),
                                             ("ID_TABLE_MAX_EXPANDED_BYTES", "xlsx")])
def test_identity_budgets_reject_without_partial_metadata_or_files(
        client, database, make_case, monkeypatch, budget, extension):
    cid = make_case()
    database.x("insert into account_ids(case_id,account,id_no) values (?,'KEEP','111111111111111')", [cid])
    before = database.q("select * from account_ids")
    monkeypatch.setattr(importer, budget, 1, raising=False)
    response = upload_ids(client, cid, identities_file(2, extension), extension)
    assert response.status_code == 400, response.text
    assert database.q("select * from account_ids") == before
    assert_no_temporary_uploads(database)


def test_photo_read_is_bounded_and_rejection_keeps_previous_photo(
        client, database, make_case, monkeypatch):
    cid, content = make_case(), photo_bytes()
    assert upload_photo(client, cid, content).status_code == 200
    before = database.q("select * from account_photos")
    directory = database.DATA_DIR / "photos" / f"c{cid}"
    files_before = {p.name: p.read_bytes() for p in directory.iterdir()}
    limit = len(content) - 1
    monkeypatch.setattr(main, "MAX_PHOTO_BYTES", limit, raising=False)
    original = UploadFile.read
    async def bounded_read(self, size=-1):
        assert 0 < size <= limit + 1, "photo read must request only its budget plus one byte"
        return await original(self, size)
    monkeypatch.setattr(UploadFile, "read", bounded_read)
    response = upload_photo(client, cid, content)
    assert response.status_code == 400, response.text
    assert database.q("select * from account_photos") == before
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == files_before


def test_photo_decode_budget_rejects_before_publication(client, database, make_case, monkeypatch):
    cid = make_case()
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 32)
    response = upload_photo(client, cid, photo_bytes())
    assert response.status_code == 400, response.text
    assert database.q("select * from account_photos where case_id=?", [cid]) == []
    assert not (database.DATA_DIR / "photos" / f"c{cid}").exists()


def test_exact_upload_boundaries_still_accept_legal_inputs(
        client, database, make_case, flow_row, monkeypatch):
    cid = populated_case(database, make_case, flow_row)
    package = exported(client, cid)
    monkeypatch.setattr(case_packages, "MAX_PACKAGE_BYTES", len(package), raising=False)
    assert import_package(client, package).status_code == 200
    content = identities_file()
    monkeypatch.setattr(importer, "ID_TABLE_MAX_BYTES", len(content), raising=False)
    assert upload_ids(client, cid, content).status_code == 200
    assert_no_temporary_uploads(database)
    photo = photo_bytes()
    monkeypatch.setattr(main, "MAX_PHOTO_BYTES", len(photo), raising=False)
    assert upload_photo(client, cid, photo).status_code == 200


@pytest.mark.parametrize("encoding", ["utf-8-sig", "gb18030", "utf-16"])
def test_identity_text_encodings_keep_exact_account_strings(client, make_case, encoding):
    cid = make_case()
    content = "卡号,身份证号\n00012345678901234567890,123456789012345\n".encode(encoding)
    response = upload_ids(client, cid, content)
    assert response.status_code == 200, response.text
    assert client.get("/api/account-ids/list", params={"case_id": cid}).json()[0]["account"] == "00012345678901234567890"


def test_identity_workbook_skips_notes_sheet_and_keeps_numeric_id(client, make_case):
    cid, output = make_case(), io.BytesIO()
    with pd.ExcelWriter(output) as workbook:
        pd.DataFrame([["合成说明"]]).to_excel(workbook, index=False, header=False, sheet_name="说明")
        pd.DataFrame([["合成表"], ["卡号", "身份证号"], ["000123", 123456789012345]]).to_excel(
            workbook, index=False, header=False, sheet_name="身份资料")
    response = upload_ids(client, cid, output.getvalue(), "xlsx")
    assert response.status_code == 200, response.text
    row = client.get("/api/account-ids/list", params={"case_id": cid}).json()[0]
    assert (row["account"], row["id_no"]) == ("000123", "123456789012345")
