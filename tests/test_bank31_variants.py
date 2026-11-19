"""Independent synthetic expectations for native bank31 source variants."""
import csv
import io
import json
import zipfile

from openpyxl import load_workbook
import pytest
from fastapi.testclient import TestClient

from app import import_preview_cache, table_archive, table_import
from app.main import app
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
@pytest.mark.parametrize("kind", ["drop-empty-ip", "empty-extra"])
def test_blank_source_currency_in_bank31_variants_is_excluded_from_funds_and_report(client, make_case, archive, kind):
    headers, source_row = csv.reader(io.StringIO(data(kind).decode("utf-8-sig")))
    known = dict(zip(headers, source_row))
    missing = {**known, "交易金额": "12.34", "交易币种": ""}
    known.update({"交易流水号": "P002", "交易金额": "12.34", "交易币种": "JPY"})
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows([headers, [missing[h] for h in headers], [known[h] for h in headers]])
    content = stream.getvalue().encode("utf-8-sig")
    if archive:
        package = io.BytesIO()
        with zipfile.ZipFile(package, "w") as output:
            output.writestr("source.csv", content)
        content = package.getvalue()

    cid = make_case("SYN bank31 source currency")
    view, job = imported(client, cid, content, archive)
    base = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    member = "source.csv" if archive else "table.csv"
    page = client.get(f"/api/v1/cases/{cid}/flows").json()
    records = client.get(base + "/records").json()["items"]
    report_response = client.get(base + "/report", params={"member_path": member})
    assert report_response.status_code == 200, report_response.text
    report = report_response.json()
    assert (view["stats"]["ok"], job["result"]["ok"]) == (1, 1)
    assert [(r["source_row"], r["disposition"], r["reason_code"]) for r in records] == [
        (2, "unresolved", "invalid_currency"), (3, "imported", "transaction_valid")]
    assert records[0]["flow_id"] is None
    assert [(row["trade_no"], row["currency"], row["amount"]) for row in page["items"]] == [("P002", "JPY", "12.34")]
    assert report["summary"]["transaction_count"] == 1
    assert report["summary"]["out_amount"] == "12.34"
    assert report["quality"]["dispositions"] == {"imported": 1, "unresolved": 1}
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == content


def test_manual_reference_selection_cannot_authorize_nonempty_bank31_extension(client, make_case):
    cid = make_case("SYN bank31 occupied extension")
    base = f"/api/v1/cases/{cid}/imports"
    response = client.post(base + "/preview", files={"file": ("source.csv", data("nonempty-extra"))})
    assert response.status_code == 200, response.text
    view = response.json()
    assert (view["table_summary"]["profile"], view["stats"]["ok"]) == ("funds-trace-table-v78", 0)
    response = client.post(f"{base}/{view['import_id']}/preview", json={
        "mode": view["mode"], "mapping": view["mapping"], "reference_policy": "transaction-reference-v1"})
    assert response.status_code == 200, response.text
    assert response.json()["stats"]["reason_counts"] == {"conflicting_field": 1}


def extension_currency_matrix():
    headers = [{"交易金额": "交易金额(分)", "交易余额": "交易余额(人民币元)"}.get(name, name)
               for name in HEADERS if name != "IP地址"] + ["附加说明"]
    rows = []
    for index, (amount, currency, balance, extension) in enumerate([
        ("1234", "", "20.00", "SYN unknown meaning"),
        ("1234", "XYZ-INVALID", "20.00", "SYN unknown meaning"),
        ("1234", "JPY", "", "SYN unknown meaning"),
        ("9007199254740991", "JPY", "", ""),
    ], 1):
        values = {**VALUES, "交易流水号": f"SYN-{index}", "交易金额(分)": amount, "交易币种": currency,
                  "交易余额(人民币元)": balance, "附加说明": extension}
        rows.append([values.get(name, "") for name in headers])
    return [headers, *rows]


def matrix_csv(matrix):
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(matrix)
    return stream.getvalue().encode("utf-8-sig")


def preview_extension_currency(client, cid, route):
    from test_table_multisheet_append import encoded
    from xls_fixtures import workbook

    matrix = extension_currency_matrix()
    bank_sheet = ("SYN bank source", matrix)
    member = "table.csv"
    if route in {"xls", "xls-zip"}:
        name, content = "bank.xls", workbook([bank_sheet])
        member = "worksheets/0001"
    elif route == "xlsx-evidence":
        events = ("SYN event source", [
            ["date", "payer_account", "payee_account", "amount",
             "参考号机构", "参考号渠道", "参考号业务域", "跨账簿事件参考号"],
            ["2026-01-02", "SYN-EVENT-A", "SYN-EVENT-B", "1.23",
             "Synthetic bank", "SYN ledger", "SYN environment", "SYN event"],
        ])
        name, content = "bank.xlsx", encoded([bank_sheet, events], ".xlsx")
        member = "worksheets/0001"
    else:
        name, content = "bank.csv", matrix_csv(matrix)
    endpoint = f"/api/v1/cases/{cid}/imports"
    if route in {"zip", "xls-zip"}:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as package:
            package.writestr(name, content)
        response = client.post(endpoint + "/preview", files={"file": ("bank.zip", stream.getvalue())})
        member = name if route == "zip" else f"{name}/{member}"
    elif route == "directory":
        member = f"SYN branch/{name}"
        response = client.post(endpoint + "/directory/preview", files=[("files", (member, content))])
    else:
        response = client.post(endpoint + "/preview", files={"file": (name, content)})
    assert response.status_code == 200, response.text
    return response.json(), member


@pytest.mark.parametrize("route,profile", [
    ("csv", "funds-trace-table-v78"),
    ("xls", "funds-trace-table-v79"),
    ("xlsx-evidence", "funds-trace-table-v80"),
    ("zip", "funds-trace-table-archive-v66"),
    ("directory", "funds-trace-table-archive-v67"),
    ("xls-zip", "funds-trace-table-archive-v68"),
])
def test_bank31_conflicting_extension_preserves_source_currency_after_repreview(client, make_case, route, profile):
    cid = make_case("SYN bank31 source currency with extension")
    case = f"/api/v1/cases/{cid}"
    view, member = preview_extension_currency(client, cid, route)
    task = f"{case}/imports/{view['import_id']}"
    if route == "csv":
        response = client.post(task + "/preview", json={
            "mode": view["mode"], "mapping": view["mapping"], "reference_policy": "transaction-reference-v1"})
        assert response.status_code == 200, response.text
        view = response.json()
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert view["table_summary"]["profile"] == job["result"]["table_summary"]["profile"] == profile
    response = client.get(task + "/records", params={"member_path": member})
    assert response.status_code == 200, response.text
    records = response.json()["items"]
    assert [(row["source_row"], row["disposition"], row["reason_code"]) for row in records] == [
        (2, "unresolved", "conflicting_field"), (3, "unresolved", "conflicting_field"),
        (4, "unresolved", "conflicting_field"), (5, "imported", "transaction_valid")]
    assert [row["flow_id"] for row in records[:3]] == [None, None, None]
    assert [row["details"]["validation_issues"] for row in records[:3]] == [
        "conflicting_field", "conflicting_field,invalid_currency", "conflicting_field"]
    snapshots = [json.loads(row["details"]["source_ledger_evidence"]) for row in records]
    assert [(value["source_currency"], value["row"]["currency"], value["row"]["amount"])
            for value in snapshots] == [
        ("", "", "12.34"), ("", "", "12.34"), ("JPY", "JPY", "12.34"),
        ("JPY", "JPY", "90071992547409.91")]
    assert [row["details"]["currency_basis"] for row in records[:2]] == [
        "来源币种未明确，按原始记录保留", "来源币种未明确，按原始记录保留"]
    flows = client.get(case + "/flows").json()["items"]
    expected = {"SYN-4": ("JPY", "90071992547409.91")}
    if route == "xlsx-evidence":
        expected[""] = ("CNY", "1.23")
    assert {row["trade_no"]: (row["currency"], row["amount"]) for row in flows} == expected


@pytest.mark.parametrize("route,previous", [("csv", "funds-trace-table-v75"), ("zip", "funds-trace-table-archive-v63")])
def test_frozen_bank31_extension_currency_survives_reopen_and_package(database, make_case, monkeypatch, route, previous):
    cid = make_case("SYN saved bank31 source currency")
    case = f"/api/v1/cases/{cid}"
    parse = imports._parse

    def historical(*args, **options):
        return parse(*args, **{**options, "table_profile": previous, "_auto_event_profile": False})

    with TestClient(app) as client, monkeypatch.context() as old:
        old.setattr(imports, "_parse", historical)
        saved, member = preview_extension_currency(client, cid, route)
    assert saved["table_summary"]["profile"] == previous
    import_preview_cache.discard(saved["import_id"])
    task = f"{case}/imports/{saved['import_id']}"
    with database._lock:
        database._con.close()
        database._con = None
    with TestClient(app) as client:
        if route == "csv":
            response = client.post(task + "/preview", json={
                "mode": saved["mode"], "mapping": saved["mapping"], "reference_policy": "transaction-reference-v1"})
            assert response.status_code == 200, response.text
            saved = response.json()
            assert saved["table_summary"]["profile"] == previous
        old_job = complete(client, cid, saved)
        assert old_job["stage"] == "done", old_job
        old_url = f"{case}/sources/{saved['import_id']}/report"
        old_journal = client.get(old_url).json()
        assert old_journal["profile"] == previous

        current, _ = preview_extension_currency(client, cid, route)
        current_job = complete(client, cid, current)
        assert current_job["stage"] == "done", current_job
        assert (current_job["result"]["ok"], current_job["result"]["rows_dup"]) == (0, 1)
        assert client.get(task).json()["result"] == old_job["result"]
        assert client.get(old_url).json() == old_journal
        current_url = f"{case}/sources/{current['import_id']}/report"
        current_journal = client.get(current_url).json()
        assert current_journal["profile"] == ("funds-trace-table-v78" if route == "csv" else "funds-trace-table-archive-v66")

        journals = {journal["profile"]: journal for journal in (old_journal, current_journal)}
        for profile, journal in journals.items():
            record = journal["items"][0]
            ledger = json.loads(record["details"]["source_ledger_evidence"])
            assert (record["member_path"], record["source_row"], record["flow_id"], record["reason_code"]) == (
                member, 2, None, "conflicting_field")
            assert ledger["source_currency"] == ""
            assert ledger["row"]["currency"] == ("CNY" if profile == previous else "")

        original = client.get(f"/api/cases/{cid}/export")
        assert original.status_code == 200, original.text
        restored = client.post("/api/cases/import", files={"file": ("bank31.fundcase", original.content)})
        assert restored.status_code == 200, restored.text
        restored_case = f"/api/v1/cases/{restored.json()['case_id']}"
        row, = client.get(restored_case + "/flows").json()["items"]
        assert (row["trade_no"], row["currency"], row["amount"]) == ("SYN-4", "JPY", "90071992547409.91")
        sources = client.get(f"{restored_case}/flows/{row['id']}/sources").json()["items"]
        assert {source["parser_version"] for source in sources} == set(journals)
        for source in sources:
            restored_report = client.get(f"{restored_case}/sources/{source['source_id']}/report")
            assert restored_report.status_code == 200, restored_report.text
            journal = restored_report.json()
            assert journal["source_available"] is False
            assert journal["profile"] == source["parser_version"]
            assert journal["items"][:3] == journals[journal["profile"]]["items"][:3]


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


@pytest.mark.parametrize("suffix,archive,profile", [(".xlsx", False, "funds-trace-table-v78"),
                                                  (".xls", False, "funds-trace-table-v79"),
                                                  (".xls", True, "funds-trace-table-archive-v68")])
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
