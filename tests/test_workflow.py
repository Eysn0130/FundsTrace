"""Front-door file import, query, export and batch deletion with synthetic data."""
import io
import time

import pandas as pd
import pytest


def synthetic_file(extension):
    frame = pd.DataFrame([
        {"流水号": "SYN-001", "交易时间": "2026-01-01 10:00:00", "付款人账号": "000SYN-A",
         "付款人名称": "Synthetic sender", "收款人账号": "000SYN-B", "收款人名称": "Synthetic receiver", "交易金额": "123.45"},
        {"流水号": "SYN-002", "交易时间": "2026-01-01 10:10:00", "付款人账号": "000SYN-B",
         "付款人名称": "Synthetic receiver", "收款人账号": "000SYN-C", "收款人名称": "Synthetic next", "交易金额": "67.89"},
    ])
    if extension == "csv":
        return frame.to_csv(index=False).encode("utf-8-sig")
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False)
    return buffer.getvalue()


def upload_and_commit(client, case_id, extension):
    preview = client.post("/api/import/preview", files={"file": (f"synthetic.{extension}", synthetic_file(extension))})
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["mode"] == "A"
    assert len(body["preview"]) == 2
    response = client.post("/api/import/commit", json={
        "import_id": body["import_id"], "mode": body["mode"],
        "mapping": body["mapping"], "case_id": case_id, "mark_accounts": "",
    })
    assert response.status_code == 200, response.text
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = client.get("/api/import/progress", params={"import_id": body["import_id"], "case_id": case_id})
        assert result.status_code == 200
        job = result.json()
        if job["stage"] in ("done", "error"):
            assert job["stage"] == "done", job
            return job["result"]
        time.sleep(0.01)
    pytest.fail("Synthetic import job did not finish within ten seconds")


@pytest.mark.parametrize("extension", ["csv", "xlsx"])
def test_file_to_case_to_exports_and_scoped_batch_delete(client, make_case, extension):
    cid = make_case(f"Synthetic {extension}")
    other = make_case("Unrelated synthetic case")
    result = upload_and_commit(client, cid, extension)
    assert result["ok"] == 2

    query = client.get("/api/flows", params={"case_id": cid}).json()
    assert query["total"] == 2
    assert sorted(row["amount"] for row in query["rows"]) == ["123.45", "67.89"]
    assert {row["payer_account"] for row in query["rows"]} == {"000SYN-A", "000SYN-B"}
    assert client.get("/api/flows", params={"case_id": other}).json()["total"] == 0

    tracked = client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"] for row in query["rows"]]})
    assert tracked.status_code == 200, tracked.text
    exported = client.get("/api/export/tracked.xlsx", params={"case_id": cid})
    assert exported.status_code == 200, exported.text
    sheets = pd.read_excel(io.BytesIO(exported.content), sheet_name=None)
    assert len(sheets) == 2
    assert len(next(iter(sheets.values()))) == 2

    package = client.get(f"/api/cases/{cid}/export")
    assert package.status_code == 200
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    assert restored.json()["flows"] == 2

    batch_id = result["batch_id"]
    assert client.delete(f"/api/batches/{batch_id}", params={"case_id": other}).status_code == 404
    assert client.delete(f"/api/batches/{batch_id}", params={"case_id": cid}).status_code == 200
    assert client.get("/api/flows", params={"case_id": cid}).json()["total"] == 0
    assert client.get("/api/flows", params={"case_id": restored.json()["case_id"]}).json()["total"] == 2
