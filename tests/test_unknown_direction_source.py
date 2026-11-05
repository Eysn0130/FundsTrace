"""Closed native28 source observations retain unknown direction without funds."""
import io
import json

import pytest
from openpyxl import load_workbook

from app import feedback_import, table_import
from test_folder_evidence import transaction
from test_native_bank_observations import upload_bytes
from test_source_ledger import add_source
from test_source_ledger_main_flags import graph, mark, track


def unknown(amount="12.34", **changes):
    return transaction(**{"交易账号": "SYN-UNORIENTED", "交易卡号": "SYN-UNORIENTED-CARD", "收付标志": "",
        "交易对手账卡号": "", "对手户名": "", "对手开户银行": "", "交易金额": amount,
        "交易流水号": "SYN-UNKNOWN-" + amount, "凭证号": "SYN-VOUCHER-" + amount,
        "摘要说明": "SYN source summary\t", "备注": "SYN separate remark", **changes})


@pytest.mark.parametrize("kind", ["csv", "zip", "xlsx", "xls"])
def test_public_unknown_direction_query_node_export_reopen_withdraw(client, make_case, kind):
    cid = make_case("SYN unknown direction source")
    raw, name = upload_bytes([unknown(), unknown("0.00", 交易时间="2026-01-01 10:01:00")], kind)
    view, job = add_source(client, cid, raw, name)
    base = f"/api/v1/cases/{cid}"
    page = client.get(base + "/flows", params={"account": "SYN-UNORIENTED"}).json()
    assert page["total"] == 2
    account_id = page['items'][0]['self_account']
    mark(client, cid, account_id)
    assert {row["amount"] for row in page["items"]} == {"12.34", "0.00"}
    assert client.get(base + "/flows").json()["total"] == 2
    assert client.get("/api/flows", params={"case_id": cid, "account": "SYN-UNORIENTED", "account_exact": True}).json()["total"] == 2
    registry = client.get("/api/account-registry", params={"case_id": cid}).json()
    assert registry["total"] == 1 and registry["items"][0]["account"] == account_id
    assert registry['items'][0]['account_number'] == 'SYN-UNORIENTED'
    profile = client.get("/api/account-registry/profile", params={"case_id": cid, "account": account_id})
    assert profile.status_code == 200 and profile.json()["source_ledger"]["count"] == 2
    for row in page["items"]:
        assert row["payer_account"] == row["payee_account"] == ""
        assert row["raw_self_account"] == "SYN-UNORIENTED" and row["summary"] == "SYN source summary\t"
        info = row["source_ledger"]
        assert info["direction"] == "unknown" and info["source_direction"] == ""
        assert (info["economic_effect"], info["economic_amount"]) == (("unknown", None) if row["amount"] == "12.34" else ("no_movement", "0.00"))
    stats = client.get(base + "/stats").json()
    assert stats["flows"] == 0 and stats["amount_total"] == "0.00"
    assert stats["accounts"] == 1 and stats["source_ledger"]["count"] == 2
    assert stats["import_coverage"]["excluded_flows"] == 0
    track(client, cid, page["items"])
    result = graph(client, cid)
    node, = result["nodes"]
    assert node["account"] == account_id and node["is_main"] == 1
    assert node["in_amt"] == node["out_amt"] == "0.00"
    assert result["edges"] == [] and result["transaction_count"] == 0
    response = client.get("/api/export/tracked.xlsx", params={"case_id": cid})
    assert response.status_code == 200, response.text
    book = load_workbook(io.BytesIO(response.content), data_only=True)
    records = list(book["追踪流水"].values);headers = records[0];exported = [dict(zip(headers, row)) for row in records[1:]]
    assert {str(row["交易金额"]) for row in exported} == {"12.34", "0.00"}
    assert all(row["本方账号"] == "SYN-UNORIENTED" and row["方向状态"] == "unknown" for row in exported)
    assert all(row["付款账号"] is None and row["收款账号"] is None and row["来源标注方向"] is None for row in exported)
    packed = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("unknown.fundcase", packed.content)})
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/v1/cases/{restored.json()['case_id']}/flows", params={"account": "SYN-UNORIENTED"}).json()["total"] == 2
    assert client.delete(f"/api/batches/{job['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert client.get(base + "/flows").json()["total"] == 0


@pytest.mark.parametrize("changes", [{"交易时间": ""}, {"交易币种": ""}, {"交易账号": "", "交易卡号": ""},
                                    {"交易金额": "NaN"}])
def test_unknown_direction_does_not_admit_other_missing_fields(client, make_case, changes):
    cid = make_case("SYN unknown direction rejection")
    raw, name = upload_bytes([unknown(**changes)])
    view, _ = add_source(client, cid, raw, name)
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0
    records = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()["items"]
    assert records[0]["disposition"] == "unresolved" and records[0]["flow_id"] is None


def test_old_native28_profile_does_not_reinterpret_unknown_direction():
    from app.feedback_import import COLUMNS
    from test_feedback31_reference import payload
    raw = payload([unknown()], COLUMNS)
    for profile in (table_import.NATIVE_OBSERVATION_PROFILE, table_import.NATIVE_OBSERVATION_EVIDENCE_PROFILE):
        plan = table_import.parse(raw, suffix=".csv", profile=profile)
        assert not plan["rows"] and "source_ledger_evidence" not in plan["records"][0]["details"]


def test_true_conflicting_money_cannot_become_unknown_direction_ledger(client, make_case):
    from app.feedback_import import COLUMNS
    from test_feedback31_reference import payload
    cid = make_case("SYN real money conflict")
    raw = payload([unknown(**{"amount": "99.99"})], COLUMNS + ("amount",))
    view, _ = add_source(client, cid, raw)
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()["items"][0]["disposition"] == "unresolved"


def test_old_ready_receipt_package_and_explicit_reimport(client, database, make_case, monkeypatch):
    from app.services import imports
    from test_automatic_table_import import preview
    from test_folder_evidence import complete
    cid = make_case("SYN old unknown direction ready")
    raw, name = upload_bytes([unknown(), unknown("0.00")])
    with monkeypatch.context() as old:
        old.setattr(imports, "TABLE_PROFILE", table_import.NATIVE_OBSERVATION_PROFILE)
        ready = preview(client, cid, raw, name)
    refreshed = client.post(f"/api/v1/cases/{cid}/imports/{ready['import_id']}/preview",
                           json={"mode": ready["mode"], "mapping": ready["mapping"]})
    assert refreshed.status_code == 200
    assert refreshed.json()["table_summary"]["profile"] == table_import.NATIVE_OBSERVATION_PROFILE
    assert complete(client, cid, refreshed.json())["stage"] == "done"
    receipt_url = f"/api/v1/cases/{cid}/imports/{ready['import_id']}"
    receipt = client.get(receipt_url).json()
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0
    packed = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("old-unknown.fundcase", packed.content)})
    assert restored.status_code == 200, restored.text
    restored_id = restored.json()["case_id"]
    assert client.get(f"/api/v1/cases/{restored_id}/flows").json()["total"] == 0
    with database._lock:
        database._con.close()
        database._con = None
    assert client.get(receipt_url).json() == receipt
    _, current = add_source(client, cid, raw, name)
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 2
    assert client.get(receipt_url).json() == receipt
    assert client.delete(f"/api/batches/{current['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0


def test_bound_own_account_card_query_and_graph_keep_unknown_separate(client, make_case):
    from test_feedback31_reference import payload
    from test_folder_evidence import accepted, account, table
    cid = make_case("SYN typed unknown direction own")
    rows = [unknown(), unknown("0.00"), transaction(**{"交易账号": "SYN-UNORIENTED", "交易卡号": "SYN-UNORIENTED-CARD"})]
    accepted(client, cid, [("SYN-REQUEST/Synthetic bank交易明细信息.csv", payload(rows, feedback_import.COLUMNS)),
        ("SYN-REQUEST/Synthetic bank账户信息.csv", table("account", [account("SYN-UNORIENTED", "SYN-UNORIENTED-CARD")])),
        ("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank"}]))], scope_bank=None)
    base = f"/api/v1/cases/{cid}"
    for alias in ("SYN-UNORIENTED", "SYN-UNORIENTED-CARD"):
        page = client.get(base + "/flows", params={"account": alias}).json()
        assert page["total"] == 3
        legacy = client.get("/api/flows", params={"case_id": cid, "account": alias}).json()
        assert legacy["total"] == 3
    track(client, cid, [row for row in page["items"] if row["source_ledger"]])
    result = graph(client, cid)
    node, = result["nodes"]
    assert node["account_number"] == "SYN-UNORIENTED"
    assert node["in_amt"] == node["out_amt"] == "0.00" and result["edges"] == []
    assert client.get(base + "/stats").json()["amount_total"] == "10.25"


def test_unknown_direction_retains_prior_balance_uncertainty_for_known_postings(client, make_case):
    cid = make_case("SYN unknown direction cannot bridge balances")
    rows = [unknown("10.00", 收付标志="出", 交易对手账卡号="SYN-COUNTER", 交易余额="100.00"),
            unknown("12.34", 交易时间="2026-01-01 10:01:00", 交易余额="90.00"),
            unknown("-2.30", 交易时间="2026-01-01 10:02:00", 收付标志="出", 交易对手账卡号="SYN-COUNTER", 交易余额="102.30")]
    raw, name = upload_bytes(rows)
    add_source(client, cid, raw, name)
    page = client.get(f"/api/v1/cases/{cid}/flows").json()
    posting = next(row for row in page["items"] if row["amount"] == "-2.30")
    change = posting["source_ledger"]["balance_change"]
    assert change["status"] == "unknown" and change["difference"] is None
    assert change["reason"] == "unlocated_source_record"
