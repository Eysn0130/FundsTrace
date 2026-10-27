"""Public import/track/delete/export journeys retain facts and literal text."""
import csv
import io
import time

import openpyxl


def imported(client, cid, headers, rows):
    content = io.StringIO()
    writer = csv.writer(content)
    writer.writerow(headers)
    writer.writerows(rows)
    preview = client.post(f"/api/v1/cases/{cid}/imports/preview",
                          files={"file": ("synthetic.csv", content.getvalue().encode())})
    assert preview.status_code == 200, preview.text
    preview = preview.json()
    base = f"/api/v1/cases/{cid}/imports/{preview['import_id']}"
    assert client.post(base + "/commit", json={"mode": preview["mode"], "mapping": preview["mapping"]}).status_code == 200
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = client.get(base).json()
        if job["stage"] in ("done", "error"):
            assert job["stage"] == "done", job
            return job["result"]
        time.sleep(.01)
    raise AssertionError("Import did not finish")


def workbook(client, cid, canvas_id=0):
    response = client.get("/api/export/tracked.xlsx", params={"case_id": cid, "canvas_id": canvas_id})
    assert response.status_code == 200, response.text
    return openpyxl.load_workbook(io.BytesIO(response.content), data_only=False)


def records(sheet):
    values = list(sheet.values)
    return [dict(zip(values[0], row)) for row in values[1:]]


def track(client, cid):
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [r["id"] for r in flows]}).status_code == 200
    return flows


def test_pending_accounts_follow_remaining_own_ledger_not_stale_or_manual_roles(client, make_case):
    cid = make_case("Synthetic pending ledgers")
    headers = ["交易时间", "本方账号", "对方账号", "收支标志", "金额"]
    own = imported(client, cid, headers, [["2026-01-01 10:00:00", "SYN-A", "SYN-C", "支出", "0.01"]])
    imported(client, cid, headers, [["2026-01-01 11:00:00", "SYN-B", "SYN-A", "支出", "0.02"],
                                  ["2026-01-01 12:00:00", "SYN-B", "SYN-MANUAL", "支出", "0.03"]])
    # A role is a display/business classification, not proof of owning a ledger.
    assert client.post("/api/main-accounts", json={"case_id": cid, "account": "SYN-MANUAL", "name": "Synthetic manual"}).status_code == 200
    track(client, cid)
    assert {r["账号"] for r in records(workbook(client, cid)["待调取账号"])} == {"SYN-C", "SYN-MANUAL"}
    assert client.delete(f"/api/batches/{own['batch_id']}", params={"case_id": cid}).status_code == 200
    after = records(workbook(client, cid)["待调取账号"])
    assert {r["账号"] for r in after} == {"SYN-A", "SYN-MANUAL"}
    assert next(r for r in after if r["账号"] == "SYN-A")["交易总额"] == "0.00—0.02"
    roles = client.get("/api/main-accounts", params={"case_id": cid}).json()
    assert {r["account"] for r in roles} >= {"SYN-A", "SYN-B", "SYN-MANUAL"}
    assert next(r for r in roles if r["account"] == "SYN-MANUAL")["source"] == "manual"
    # A different case's A ledger cannot hide the current case's missing ledger.
    other = make_case("Other synthetic ledger")
    imported(client, other, headers, [["2026-01-01 10:00:00", "SYN-A", "SYN-Z", "收入", "0.04"]])
    assert {r["账号"] for r in records(workbook(client, cid)["待调取账号"])} == {"SYN-A", "SYN-MANUAL"}
    # A new real A ledger resolves it, independently of which canvas tracks it.
    imported(client, cid, headers, [["2026-01-02 10:00:00", "SYN-A", "SYN-Z", "收入", "0.05"]])
    assert {r["账号"] for r in records(workbook(client, cid)["待调取账号"])} == {"SYN-MANUAL"}


def test_excel_all_imported_strings_remain_literal_on_both_sheets(client, make_case):
    cid = make_case("Synthetic literal export")
    headers = ["交易时间", "交易流水号", "付款方户名", "付款方账号", "付款开户行",
               "收款方户名", "收款方账号", "收款开户行", "金额", "摘要", "渠道"]
    literal = ["=1+1", "=SYN-PAYER", "=SYN-A", "=SYN-BANK-A", "=SYN-PAYEE", "=SYN-B", "=SYN-BANK-B"]
    imported(client, cid, headers, [["2026-01-01 10:00:00", *literal, "900719925474099.91", "=SUM(1,2)", "=SYN-CHANNEL"],
                                  ["2026-01-01 11:00:00", "#N/A", "+literal", "@account", "-bank", "Synthetic", "SYN-C", "", "0.01", "#N/A", "+literal"]])
    flows = track(client, cid)
    wb = workbook(client, cid)
    for sheet in wb:
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str):
                    assert cell.data_type == "s", (sheet.title, cell.coordinate, cell.value, cell.data_type)
    data = records(wb["追踪流水"])
    first = next(r for r in data if r["交易流水号"] == "=1+1")
    assert [first[k] for k in ["交易流水号", "付款户名", "付款账号", "付款开户行", "收款户名", "收款账号", "收款开户行"]] == literal
    assert first["摘要"] == "=SUM(1,2)" and first["渠道"] == "=SYN-CHANNEL"
    assert first["交易金额"] == "900719925474099.91"
    assert {r["流水ID"] for r in data} == {r["id"] for r in flows}
    pending = records(wb["待调取账号"])
    assert {r["账号"] for r in pending} >= {"=SYN-A", "=SYN-B"}
    assert sum(r["户名"] == "=SYN-PAYEE" for r in pending) == 1
