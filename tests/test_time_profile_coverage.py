"""Public coverage must interpret newly registered profiles without erasing unknown codes."""
import csv
import io

import pytest
from openpyxl import Workbook

from test_feedback_import_api import complete
from test_folder_evidence import archive


def rows():
    return [["交易时间", "payer account", "payee account", "amount", "currency", "交易流水号", "交易是否成功"],
            ["2026-02-03 10:00:00", "SYN-COVER-A", "SYN-COVER-B", "0.00", "CNY", "SYN-COVER-ZERO", "01"],
            ["2026-02-03 10:00:01", "SYN-COVER-A", "SYN-COVER-B", "12.34", "CNY", "SYN-COVER-POSITIVE", "01"]]


def preview(client, cid, kind):
    content = rows()
    if kind == "xlsx":
        book = Workbook(); sheet = book.active
        for row in content:
            sheet.append(row)
        data = io.BytesIO(); book.save(data); book.close(); data = data.getvalue()
    elif kind == "xls":
        from xls_fixtures import workbook
        data = workbook([("Synthetic coverage", content)])
    else:
        stream = io.StringIO(newline=""); csv.writer(stream).writerows(content)
        data = stream.getvalue().encode("utf-8-sig")
    base = f"/api/v1/cases/{cid}/imports"
    if kind == "directory":
        response = client.post(base + "/directory/preview", files=[("files", ("coverage.csv", data))])
    else:
        response = client.post(base + "/preview", files={"file": ("coverage." + kind,
            archive([("coverage.csv", data)]) if kind == "zip" else data)})
    assert response.status_code == 200, response.text
    return response.json()


def assert_coverage(client, cid, unresolved=0, non_monetary=1):
    response = client.get(f"/api/v1/cases/{cid}/stats")
    assert response.status_code == 200, response.text
    stats = response.json(); coverage = stats["import_coverage"]
    assert stats["flows"] == 1
    assert (coverage["unresolved_records"], coverage["non_monetary_records"]) == (unresolved, non_monetary)
    assert coverage["financial_interpretation_complete"] is (unresolved == 0)


@pytest.mark.parametrize("kind", ["csv", "xlsx", "xls", "zip", "directory"])
def test_current_time_profile_coverage_survives_package_and_reopen(client, database, make_case, kind):
    cid = make_case("Synthetic new profile coverage")
    view = preview(client, cid, kind)
    assert complete(client, cid, view)["stage"] == "done"
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    assert [row["disposition"] for row in records] == ["non_monetary", "imported"]
    assert all(row["details"]["reported_status"] == "unknown" for row in records)
    assert_coverage(client, cid)
    package = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    assert_coverage(client, restored.json()["case_id"])
    with database._lock:
        database._con.close(); database._con = None
    assert_coverage(client, cid)


def test_future_unknown_profile_remains_unresolved(client, database, make_case):
    cid = make_case("Synthetic future profile")
    view = preview(client, cid, "csv")
    assert complete(client, cid, view)["stage"] == "done"
    database.con().execute("update import_sources set parser_version='funds-trace-table-v999' where case_id=?", [cid])
    assert_coverage(client, cid, unresolved=2, non_monetary=0)


def test_new_xls_profile_is_not_accepted_for_another_suffix(client, database, make_case):
    cid = make_case("Synthetic XLS profile suffix guard")
    view = preview(client, cid, "xls")
    assert complete(client, cid, view)["stage"] == "done"
    database.con().execute("update import_sources set filename='synthetic.csv' where case_id=?", [cid])
    assert_coverage(client, cid, unresolved=2, non_monetary=0)
