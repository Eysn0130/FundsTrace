"""Front-door file import, query, export and batch deletion with synthetic data."""
import io
import time

import pandas as pd
import pytest

from app import importer
from app.services import imports


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
@pytest.mark.parametrize("legacy_without_journal", [False, True])
def test_file_to_case_to_exports_and_scoped_batch_delete(client, make_case, monkeypatch, extension, legacy_without_journal):
    cid = make_case(f"Synthetic {extension}")
    other = make_case("Unrelated synthetic case")
    with monkeypatch.context() as profile:
        if legacy_without_journal:
            profile.setattr(imports, "TABLE_PROFILE", importer.PARSER_VERSION)
        result = upload_and_commit(client, cid, extension)
    assert result["ok"] == 2
    assert ("table_summary" in result) is (not legacy_without_journal)

    query = client.get("/api/flows", params={"case_id": cid}).json()
    assert query["total"] == 2
    assert sorted(row["amount"] for row in query["rows"]) == ["123.45", "67.89"]
    assert {row["payer_account"] for row in query["rows"]} == {"000SYN-A", "000SYN-B"}
    assert client.get("/api/flows", params={"case_id": other}).json()["total"] == 0

    tracked = client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"] for row in query["rows"]]})
    assert tracked.status_code == 200, tracked.text
    exported = client.get("/api/export/tracked.xlsx", params={"case_id": cid})
    assert exported.status_code == 200, exported.text
    sheets = pd.read_excel(io.BytesIO(exported.content), sheet_name=None, dtype=str, keep_default_na=False)
    expected_sheets = ["追踪流水", "待调取账号"] + ([] if legacy_without_journal else ["统计口径"])
    assert list(sheets) == expected_sheets
    ledger = sheets["追踪流水"]
    assert len(ledger) == 2
    assert list(ledger["交易流水号"]) == ["SYN-001", "SYN-002"]
    assert list(ledger["交易金额"]) == ["123.45", "67.89"]
    assert list(ledger["付款账号"]) == ["000SYN-A", "000SYN-B"]
    assert set(ledger["流水ID"]) == {str(row["id"]) for row in query["rows"]}
    pending = sheets["待调取账号"]
    assert pending[["账号", "转出笔数", "转出金额", "转入笔数", "转入金额", "交易总额"]].to_dict("records") == [
        {"账号": "000SYN-B", "转出笔数": "1", "转出金额": "67.89", "转入笔数": "1", "转入金额": "123.45", "交易总额": "191.34"},
        {"账号": "000SYN-A", "转出笔数": "1", "转出金额": "123.45", "转入笔数": "0", "转入金额": "0.00", "交易总额": "123.45"},
        {"账号": "000SYN-C", "转出笔数": "0", "转出金额": "0.00", "转入笔数": "1", "转入金额": "67.89", "交易总额": "67.89"}]
    if not legacy_without_journal:
        assert list(sheets["统计口径"].columns) == ["项目", "说明"]
        notes = dict(sheets["统计口径"].itertuples(index=False, name=None))
        assert notes == {
            "流水口径": "当前画布的有效追踪流水；已匹配的冲正／撤销双方退出统计，普通退款仍保留，不代表结算净额或完整清算结果。",
            "覆盖范围": "以下完整性仅限本案已导入反馈来源的资金语义，不表示银行提供的数据齐全。",
            "资金效果未确定记录数": "0", "非资金事件记录数": "0", "已匹配冲正撤销事件数": "0",
            "保留原始流水中退出分析的笔数": "0", "已导入来源资金语义是否完整": "是",
            "未确定记录": "资金效果未确定的记录未计入流水统计；非资金事件单独保留，不计入金额与交易笔数。"}

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
