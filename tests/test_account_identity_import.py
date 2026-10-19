"""Synthetic identity-table imports preserve case and transaction boundaries."""
import io

import pandas as pd
import pytest

from app import main
from test_cases import insert_flow


def identity_file(rows, extension="csv"):
    frame = pd.DataFrame(rows, columns=["卡号", "身份证号"])
    if extension == "xlsx":
        output = io.BytesIO()
        frame.to_excel(output, index=False)
        return output.getvalue()
    return frame.to_csv(index=False, sep="\t" if extension in {"tsv", "txt"} else ",").encode("utf-8-sig")


def upload(client, case_id, rows, extension="csv"):
    return client.post("/api/account-ids/import", data={"case_id": case_id}, files={
        "file": (f"synthetic-identities.{extension}", identity_file(rows, extension)),
    })


def identities(client, case_id):
    response = client.get("/api/account-ids/list", params={"case_id": case_id})
    assert response.status_code == 200, response.text
    return {row["account"]: row["id_no"] for row in response.json()}


def save_identity(client, case_id, account, id_no):
    response = client.post("/api/account-ids/set", json={
        "case_id": case_id, "account": account, "id_no": id_no,
    })
    assert response.status_code == 200, response.text


def assert_uploads_clean(database):
    directory = database.DATA_DIR / "uploads"
    assert not directory.exists() or not list(directory.iterdir())


def test_import_overwrites_identity_across_this_cases_canvases_only(
        client, database, make_case, flow_row):
    first, second = make_case("Identity source"), make_case("Identity isolated")
    account = "00012345678901234567890"
    first_flow = insert_flow(database, first, flow_row, payer_account=account)
    second_flow = insert_flow(database, second, flow_row, payer_account=account)
    created = client.post("/api/canvases/save", json={
        "case_id": first, "name": "Synthetic saved shape", "snapshot": {"nodes": [], "edges": []},
    })
    assert created.status_code == 200, created.text
    canvas_id = created.json()["canvas_id"]
    for case_id, canvas, flow_id in [(first, 0, first_flow), (first, canvas_id, first_flow), (second, 0, second_flow)]:
        response = client.post("/api/track", json={"case_id": case_id, "canvas_id": canvas, "flow_ids": [flow_id]})
        assert response.status_code == 200, response.text
    save_identity(client, first, account, "111111111111111")
    save_identity(client, second, account, "333333333333333")
    flows_before = database.q("select * from flows order by id")
    tracking_before = database.q("select * from tracked_flows order by case_id,canvas_id,flow_id")
    shape_before = client.get(f"/api/canvases/{canvas_id}?case_id={first}").json()

    response = upload(client, first, [(account, "222222222222222")])

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "imported": 1, "invalid": 0, "blank_account": 0}
    assert identities(client, first) == {account: "222222222222222"}
    assert identities(client, second) == {account: "333333333333333"}
    for canvas in [0, canvas_id]:
        graph = client.get("/api/graph", params={"case_id": first, "canvas_id": canvas})
        assert graph.status_code == 200, graph.text
        assert next(node for node in graph.json()["nodes"] if node["account"] == account)["id_no"] == "222222222222222"
    other_graph = client.get("/api/graph", params={"case_id": second, "canvas_id": 0}).json()
    assert next(node for node in other_graph["nodes"] if node["account"] == account)["id_no"] == "333333333333333"
    assert database.q("select * from flows order by id") == flows_before
    assert database.q("select * from tracked_flows order by case_id,canvas_id,flow_id") == tracking_before
    assert client.get(f"/api/canvases/{canvas_id}?case_id={first}").json() == shape_before
    assert_uploads_clean(database)


def test_first_valid_duplicate_wins_and_blank_or_invalid_ids_do_not_clear_existing_values(
        client, database, make_case):
    case_id = make_case()
    for account in ["SYN-A", "SYN-B", "SYN-C"]:
        save_identity(client, case_id, account, "111111111111111")

    response = upload(client, case_id, [
        ("SYN-A", "invalid-first"), ("SYN-A", " 12345678901234567x "),
        ("SYN-A", "222222222222222"), ("SYN-B", ""), ("SYN-C", "invalid"),
        ("", "333333333333333"), ("SYN-D", "444444444444444"),
    ])

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "imported": 2, "invalid": 2, "blank_account": 1}
    assert identities(client, case_id) == {
        "SYN-A": "12345678901234567X", "SYN-B": "111111111111111",
        "SYN-C": "111111111111111", "SYN-D": "444444444444444",
    }
    assert_uploads_clean(database)


@pytest.mark.parametrize("extension", ["csv", "tsv", "txt", "xlsx"])
def test_supported_text_and_xlsx_imports_preserve_leading_zeros_and_long_account_strings(
        client, database, make_case, extension):
    case_id = make_case()
    rows = [("000123456789012345678901234567890", "012345678901234"),
            ("123456789012345678901234567890", "012345678901234567")]

    response = upload(client, case_id, rows, extension)

    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 2
    assert identities(client, case_id) == dict(rows)
    assert_uploads_clean(database)


def test_partial_batch_write_failure_rolls_back_replacements_and_cleans_upload(
        client, database, make_case, monkeypatch):
    case_id = make_case()
    save_identity(client, case_id, "SYN-A", "111111111111111")
    before = database.q("select * from account_ids order by case_id,account")
    execute = database.executemany
    partial_writes = []

    def fail_after_first_row(sql, rows):
        if sql.startswith("insert or replace into account_ids"):
            partial_writes.append(rows[0])
            execute(sql, rows[:1])
            raise RuntimeError("Synthetic batch-write failure")
        return execute(sql, rows)

    monkeypatch.setattr(database, "executemany", fail_after_first_row)
    response = upload(client, case_id, [("SYN-A", "222222222222222"), ("SYN-B", "333333333333333")])

    assert response.status_code == 400, response.text
    assert "Synthetic batch-write failure" in response.json()["detail"]
    assert len(partial_writes) == 1
    assert database.q("select * from account_ids order by case_id,account") == before
    assert_uploads_clean(database)


def test_missing_identity_headers_reject_without_changes_and_clean_upload(
        client, database, make_case):
    case_id = make_case()
    save_identity(client, case_id, "SYN-A", "111111111111111")
    response = client.post("/api/account-ids/import", data={"case_id": case_id}, files={
        "file": ("synthetic-wrong-header.csv", "账户,户名\nSYN-A,合成甲\n".encode("utf-8-sig")),
    })

    assert response.status_code == 400, response.text
    assert "表头" in response.json()["detail"]
    assert identities(client, case_id) == {"SYN-A": "111111111111111"}
    assert_uploads_clean(database)


def test_legacy_xls_has_actionable_rejection_without_writing_or_retaining_upload(
        client, database, make_case):
    case_id = make_case()
    save_identity(client, case_id, "SYN-A", "111111111111111")
    response = client.post("/api/account-ids/import", data={"case_id": case_id}, files={
        "file": ("synthetic-old-format.xls", b"Synthetic old workbook"),
    })

    assert response.status_code == 400, response.text
    assert ".xls" in response.json()["detail"]
    assert ".xlsx" in response.json()["detail"]
    assert identities(client, case_id) == {"SYN-A": "111111111111111"}
    assert_uploads_clean(database)


def test_case_deleted_during_parsing_cannot_be_revived_by_identity_import(
        client, database, make_case, monkeypatch):
    case_id = make_case()
    save_identity(client, case_id, "SYN-A", "111111111111111")
    parse = main.importer.read_id_table
    deletions = []

    def delete_after_parse(path):
        rows = parse(path)
        deletions.append(main.delete_package_case(case_id))
        return rows

    monkeypatch.setattr(main.importer, "read_id_table", delete_after_parse)
    response = upload(client, case_id, [("SYN-A", "222222222222222"), ("SYN-B", "333333333333333")])

    assert response.status_code == 404, response.text
    assert len(deletions) == 1
    assert not database.q("select id from cases where id=?", [case_id])
    assert not database.q("select account from account_ids where case_id=?", [case_id])
    assert_uploads_clean(database)
