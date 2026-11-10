"""Independent synthetic expectations for native bank31 source variants."""
import csv
import io
import json
import zipfile

from openpyxl import load_workbook
import pytest

from app import table_archive, table_import
from app.services import imports
from test_folder_evidence import complete
from test_source_contract_68 import HEADERS


VALUES = {"交易账号": "00000001", "交易方户名": "Synthetic", "交易对手账卡号": "00000002",
          "交易时间": "2026-01-01 02:03:04", "收付标志": "出", "交易金额": "1.25", "交易币种": "CNY",
          "交易余额": "20.00", "交易是否成功": "成功", "交易流水号": "P001", "凭证号": "V002",
          "摘要说明": "SYN original summary", "备注": "SYN independent remark"}
EXPECTED = {"base": (1, "1.25", "transaction_valid"), "reverse": (1, "1.25", "transaction_valid"),
            "drop-empty-ip": (1, "1.25", "transaction_valid"), "empty-extra": (1, "1.25", "transaction_valid"),
            "nonempty-extra": (0, "0.00", "conflicting_field"), "conflicting-primary": (0, "0.00", "conflicting_field"),
            "conflicting-amount": (0, "0.00", "conflicting_amount"), "conflicting-summary": (0, "0.00", "conflicting_field"),
            "conflicting-direction": (0, "0.00", "conflicting_direction"),
            "equal-two": (2, "2.50", "transaction_valid")}


def data(kind, archive=False):
    headers = list(HEADERS)
    row = [VALUES.get(header, "") for header in headers]
    rows = [row]
    if kind == "reverse":
        headers, rows = headers[::-1], [row[::-1]]
    elif kind == "drop-empty-ip":
        rows = [[value for header, value in zip(headers, row) if header != "IP地址"]]
        headers.remove("IP地址")
    elif kind in {"empty-extra", "nonempty-extra", "equal-two"}:
        headers.append("附加说明")
        rows = [row + ["SYN unknown meaning" if kind == "nonempty-extra" else ""]]
        if kind == "equal-two":
            rows.append(["P002" if header == "交易流水号" else VALUES.get(header, "") for header in headers])
    elif kind.startswith("conflicting-"):
        header, value = {"conflicting-primary": ("交易流水号", "P999"),
                         "conflicting-amount": ("交易金额", "2.50"),
                         "conflicting-direction": ("收付标志", "入"),
                         "conflicting-summary": ("摘要说明", "SYN conflicting summary")}[kind]
        headers.append(header)
        rows = [row + [value]]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, quoting=csv.QUOTE_ALL)
    writer.writerow(headers)
    writer.writerows(rows)
    raw = stream.getvalue().encode("utf-8-sig")
    if archive:
        package = io.BytesIO()
        with zipfile.ZipFile(package, "w") as output:
            output.writestr("source.csv", raw)
        return package.getvalue()
    return raw


def imported(client, cid, content, archive):
    response = client.post(f"/api/v1/cases/{cid}/imports/preview",
                           files={"file": ("source.zip" if archive else "source.csv", content)})
    assert response.status_code == 200, response.text
    view = response.json()
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    return view, job


@pytest.mark.parametrize("archive", [False, True], ids=["csv", "zip"])
@pytest.mark.parametrize("kind", EXPECTED)
def test_public_bank31_variants_preserve_facts_sources_graph_and_export(client, make_case, archive, kind):
    count, total, reason = EXPECTED[kind]
    cid = make_case("SYN bank31 variant " + kind)
    content = data(kind, archive)
    view, job = imported(client, cid, content, archive)
    assert view["stats"]["ok"] == count and job["result"]["ok"] == count
    page = client.get(f"/api/v1/cases/{cid}/flows").json()
    assert page["total"] == count
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    assert len(records) == (2 if kind == "equal-two" else 1)
    assert all(record["reason_code"] == reason for record in records)
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == content
    if count:
        assert client.get(f"/api/v1/cases/{cid}/stats").json()["amount_total"] == total
        assert {row["trade_no"] for row in page["items"]} == ({"P001", "P002"} if count == 2 else {"P001"})
        for row in page["items"]:
            assert (row["payer_account"], row["payee_account"], row["payer_name"], row["amount"], row["balance"],
                    row["currency"], row["summary"], row["trade_time"]) == (
                "00000001", "00000002", "Synthetic", "1.25", "20.00", "CNY", "SYN original summary", "2026-01-01 02:03:04")
            assert client.get(f"/api/v1/cases/{cid}/flows/{row['id']}/sources").json()["total"] == 1
        for record in records:
            fields = json.loads(record["details"]["source_field_evidence"])
            assert any(field["role"] == "independent_voucher_reference" and field["raw"] == "V002" for field in fields)
            assert any(field["field"] == "remark" and field["raw"] == "SYN independent remark" for field in fields)
        assert client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"] for row in page["items"]]}).status_code == 200
        graph = client.get("/api/graph", params={"case_id": cid}).json()
        assert len(graph["edges"]) == 1
        edge = graph["edges"][0]
        assert (edge["source"], edge["target"], edge["cnt"], edge["total"]) == ("00000001", "00000002", count, total)
        exported = client.get("/api/export/tracked.xlsx", params={"case_id": cid})
        assert exported.status_code == 200, exported.text
        workbook = load_workbook(io.BytesIO(exported.content), read_only=True)
        try:
            values = {str(value) for row in workbook.worksheets[0].iter_rows(values_only=True) for value in row if value is not None}
            assert {"00000001", "00000002", "P001", "SYN original summary"} <= values
        finally:
            workbook.close()
    else:
        assert all(record["disposition"] == "unresolved" and record["flow_id"] is None for record in records)
        assert not client.get("/api/graph", params={"case_id": cid}).json()["edges"]
    package = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("source.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/v1/cases/{restored.json()['case_id']}/flows").json()["total"] == count


@pytest.mark.parametrize("archive", [False, True], ids=["csv", "zip"])
def test_old_bank31_ready_session_keeps_the_original_shape_interpretation(client, make_case, monkeypatch, archive):
    cid = make_case("SYN original shape receipt")
    with monkeypatch.context() as old:
        old.setattr(imports, "TABLE_PROFILE", table_import.SOURCE_TEXT_PROFILE)
        old.setattr(table_archive, "PROFILE", table_archive.SOURCE_TEXT_PROFILE)
        response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": (
            "source.zip" if archive else "source.csv", data("drop-empty-ip", archive))})
        assert response.status_code == 200, response.text
        view = response.json()
    job = complete(client, cid, view)
    assert view["stats"]["ok"] == 0 and job["result"]["ok"] == 0
    receipt = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json()
    assert complete(client, cid, view)["stage"] == "done"
    assert client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json() == receipt
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0
    _, added = imported(client, cid, data("drop-empty-ip", archive), archive)
    assert added["result"]["ok"] == 1
    _, repeated = imported(client, cid, data("drop-empty-ip", archive), archive)
    assert repeated["result"]["ok"] == 0 and repeated["result"]["rows_dup"] == 1
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["amount_total"] == "1.25"
    assert client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json() == receipt


@pytest.mark.parametrize("column", ["IP地址", "MAC地址", "日志号", "传票号", "凭证种类", "交易柜员号",
                                    "商户名称", "交易网点名称", "交易发生地"])
def test_empty_ancillary_columns_are_optional_without_changing_transaction_roles(column):
    headers = [header for header in HEADERS if header != column]
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows([headers, [VALUES.get(header, "") for header in headers]])
    plan = table_import.parse(io.BytesIO(stream.getvalue().encode()), suffix=".csv")
    assert plan["stats"]["accepted"] == 1
    row = plan["rows"][0]
    assert (row["trade_no"], row["payer_account"], row["payee_account"], row["payer_name"], row["summary"]) == (
        "P001", "00000001", "00000002", "Synthetic", "SYN original summary")


@pytest.mark.parametrize("suffix,archive,profile", [(".xlsx", False, "funds-trace-table-v69"),
                                                  (".xls", False, "funds-trace-table-v70"),
                                                  (".xls", True, "funds-trace-table-archive-v59")])
def test_bank31_optional_shape_reaches_current_workbook_entrypoints(client, make_case, suffix, archive, profile):
    from test_table_multisheet_append import encoded
    matrix = list(csv.reader(io.StringIO(data("drop-empty-ip").decode("utf-8-sig"))))
    if suffix == ".xls":
        from xls_fixtures import workbook
        content = workbook([("SYN variant", matrix)])
    else:
        content = encoded([("SYN variant", matrix)], suffix)
    name = "source" + suffix
    if archive:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as package:
            package.writestr(name, content)
        content, name = stream.getvalue(), "source.zip"
    cid = make_case("SYN bank31 workbook variant")
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": (name, content)})
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["table_summary"]["profile"] == profile and view["stats"]["ok"] == 1
    assert complete(client, cid, view)["stage"] == "done"
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert len(rows) == 1 and (rows[0]["trade_no"], rows[0]["payer_name"], rows[0]["summary"]) == (
        "P001", "Synthetic", "SYN original summary")
