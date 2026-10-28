"""Front-door file import, query, export and batch deletion with synthetic data."""
import io
import json
from funds_assertions import unresolved_funds
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
    expected_sheets = ["追踪流水", "资金统计", "原始账簿观察", "待调取账号", "分析修订"] + ([] if legacy_without_journal else ["统计口径"])
    assert list(sheets) == expected_sheets
    assert list(sheets["分析修订"].columns) == ["项目", "说明"]
    assert isinstance(query["analysis_revision"], str) and len(query["analysis_revision"]) == 32
    assert dict(sheets["分析修订"].itertuples(index=False, name=None)) == {
        "分析修订": query["analysis_revision"],
        "金额含义": "默认追踪表按已确认事件计一次；未决行保留为观察，不能直接相加认定事件总额。资金统计给出同范围的已确认下界与可能上界；交易段总额不是跨跳本金。",
        "事件统计范围": "资金统计与本表同为当前画布选择；原始账簿观察完整保留各自金额和余额。",
        "统一资金投影": json.dumps(unresolved_funds(2, ("CNY", "191.34")), ensure_ascii=False),
        "事件证据": "无可确认事件作用域",
    }
    ledger = sheets["追踪流水"]
    assert len(ledger) == 2
    assert list(ledger["交易流水号"]) == ["SYN-001", "SYN-002"]
    assert list(ledger["交易金额"]) == ["123.45", "67.89"]
    assert list(ledger["付款账号"]) == ["000SYN-A", "000SYN-B"]
    assert set(ledger["流水ID"]) == {str(row["id"]) for row in query["rows"]}
    assert list(sheets["原始账簿观察"]["观察金额"]) == ["123.45", "67.89"]
    assert sheets["资金统计"].to_dict("records") == [{"币种": "CNY", "精确事件金额": "",
        "已确认下界": "0.00", "可能上界": "191.34", "事件数下界": "0", "事件数上界": "2",
        "所选原分录数": "2", "修订": query["analysis_revision"]}]
    pending = sheets["待调取账号"]
    assert pending[["账号", "转出笔数", "转出金额", "转入笔数", "转入金额", "交易总额"]].to_dict("records") == [
        {"账号": "000SYN-B", "转出笔数": "0—1", "转出金额": "0.00—67.89", "转入笔数": "0—1", "转入金额": "0.00—123.45", "交易总额": "0.00—191.34"},
        {"账号": "000SYN-A", "转出笔数": "0—1", "转出金额": "0.00—123.45", "转入笔数": "0", "转入金额": "0.00", "交易总额": "0.00—123.45"},
        {"账号": "000SYN-C", "转出笔数": "0", "转出金额": "0.00", "转入笔数": "0—1", "转入金额": "0.00—67.89", "交易总额": "0.00—67.89"}]
    if not legacy_without_journal:
        assert list(sheets["统计口径"].columns) == ["项目", "说明"]
        notes = dict(sheets["统计口径"].itertuples(index=False, name=None))
        assert notes == {
            "流水口径": "当前画布的有效追踪流水；已匹配的冲正／撤销双方退出统计，普通退款仍保留，不代表结算净额或完整清算结果。",
            "覆盖范围": "以下完整性仅限本案已导入反馈来源的资金语义，不表示银行提供的数据齐全。",
            "资金效果未确定记录数": "0", "非资金事件记录数": "0", "已匹配冲正撤销事件数": "0",
            "保留原始流水中退出分析的笔数": "0", "已导入来源资金语义是否完整": "是",
            "未确定记录": "资金效果未确定的记录未计入流水统计；非资金事件单独保留，不计入金额与交易笔数。",
            "统计策略": "evidence-bounded-funds-v1"}

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
