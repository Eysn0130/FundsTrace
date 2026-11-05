"""Synthetic native bank fields agree across ordinary user upload formats."""
import io
import json

import pytest

from app import feedback_import, table_archive, table_import
from test_feedback31_reference import COLUMNS as COLUMNS31, payload, row as row31
from test_folder_evidence import account, archive, table, transaction
from test_source_ledger import add_source
from test_source_ledger_main_flags import graph, mark, track


def upload_bytes(rows, kind="csv", headers=feedback_import.COLUMNS):
    if kind == "xls":
        from xls_fixtures import workbook
        raw = workbook([("Synthetic native bank", [headers, *[[row.get(key, "") for key in headers] for row in rows]])])
    else:
        raw = payload(rows, headers, "xlsx" if kind == "xlsx" else "csv")
    return (archive([("SYN-REQUEST/Synthetic bank交易明细信息.csv", raw)]), "native.zip") if kind == "zip" else (raw, "native." + kind)


@pytest.mark.parametrize("kind", ["csv", "zip", "xlsx", "xls"])
@pytest.mark.parametrize("invalid_value,invalid_reason", [("NaN", "missing_amount"), ("SYN-NOT-A-MONEY", "invalid_amount")])
def test_native28_public_reference_text_signed_source_and_invalid_amount(client, make_case, kind, invalid_value, invalid_reason):
    cid = make_case("SYN native28 source roles")
    rows = [transaction(**{"交易金额": "12.34", "交易流水号": "SYN-PRIMARY", "凭证号": "SYN-VOUCHER",
                          "凭证种类": "", "摘要说明": " SYN original summary\t", "备注": "SYN independent remark",
                          "传票号": "SYN-SLIP", "日志号": "SYN-LOG"}),
            transaction(**{"交易时间": "2026-01-01 10:01:00", "交易金额": "7.89", "交易流水号": "",
                           "凭证号": "SYN-VOUCHER-ONLY", "摘要说明": "", "备注": "SYN remark only"}),
            transaction(**{"交易时间": "2026-01-01 10:02:00", "交易金额": "-2.30", "交易流水号": "SYN-NEG",
                           "凭证号": "SYN-NEG-VOUCHER"}),
            transaction(**{"交易时间": "2026-01-01 10:03:00", "交易金额": "0.00", "交易流水号": "SYN-ZERO"}),
            transaction(**{"交易时间": "2026-01-01 10:04:00", "交易金额": invalid_value, "交易流水号": "SYN-INVALID"})]
    raw, name = upload_bytes(rows, kind)
    _, job = add_source(client, cid, raw, name)
    base = f"/api/v1/cases/{cid}"
    page = client.get(base + "/flows", params={"granularity": "observations"}).json()
    assert page["total"] == 4
    flows = {item["trade_no"]: item for item in page["items"]}
    assert {key: item["amount"] for key, item in flows.items()} == {
        "SYN-PRIMARY": "12.34", "": "7.89", "SYN-NEG": "-2.30", "SYN-ZERO": "0.00"}
    assert flows["SYN-PRIMARY"]["summary"] == " SYN original summary\t"
    assert flows[""]["summary"] == ""
    assert all(item["raw_self_account"] == "SYN-A" for item in flows.values())
    assert flows["SYN-NEG"]["source_ledger"]["signed_amount"] == "-2.30"
    assert flows["SYN-NEG"]["source_ledger"]["economic_effect"] == "unknown"
    report = client.get(base + f"/sources/{job['result']['source_id']}/report").json()["items"]
    assert len(report) == 5
    negative_reason = "cancellation_action_unproven" if kind == "zip" else "negative_amount"
    assert [item["reason_code"] for item in report] == ["transaction_valid", "transaction_valid", negative_reason, "zero_amount", invalid_reason]
    assert report[2]["disposition"] == "unresolved"
    assert report[-1]["disposition"] == "unresolved"
    evidence = {item["column"]: item for item in json.loads(report[0]["details"]["source_field_evidence"])}
    assert evidence["交易流水号"]["role"] == "source_transaction_id"
    assert evidence["凭证号"]["role"] == "independent_voucher_reference"
    for column in ("摘要说明", "备注", "传票号", "日志号"):
        assert evidence[column]["raw"] == rows[0][column]
    assert all("bank_posting_pair_evidence" not in item["details"] for item in report)
    assert client.get(base + "/stats").json()["amount_total"] == "20.23"
    assert client.get(base + f"/sources/{job['result']['source_id']}/file").content == raw
    packed = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("native.fundcase", packed.content)})
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/v1/cases/{restored.json()['case_id']}/flows", params={"granularity": "observations"}).json()["total"] == 4


@pytest.mark.parametrize("direction", ["进", "出"])
@pytest.mark.parametrize("kind", ["csv", "zip"])
def test_missing_counterparty_retains_own_node_and_exact_view_totals(client, make_case, direction, kind):
    cid = make_case("SYN one known endpoint")
    raw, name = upload_bytes([transaction(**{"收付标志": direction, "交易金额": "13.47", "交易对手账卡号": ""})], kind)
    add_source(client, cid, raw, name)
    mark(client, cid, "SYN-A")
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    track(client, cid, rows)
    result = graph(client, cid)
    node, = result["nodes"]
    assert node["account"] == "SYN-A" and node["is_main"] == 1
    assert result["edges"] == [] and result["transaction_count"] == 1
    side = "in" if direction == "进" else "out"
    assert node[side + "_amt"] == "13.47"
    assert node[("out" if side == "in" else "in") + "_amt"] == "0.00"
    view = client.post("/api/graph/view", json={"case_id": cid, "canvas_id": 0, "scope": "visible",
        "visible_accounts": ["SYN-A"], "visible_edges": [], "include_undated": True})
    assert view.status_code == 200, view.text
    assert view.json()["nodes"][0][side + "_amt"] == "13.47"
    assert view.json()["edges"] == []


def test_native28_old_reference_receipt_interpretation_is_preserved():
    raw = payload([transaction(**{"凭证号": "SYN-INDEPENDENT", "凭证种类": ""})], feedback_import.COLUMNS)
    old = table_import.parse(raw, suffix=".csv", profile=table_import.AUXILIARY_SCOPE_PROFILE)
    assert old["records"][0]["reason_code"] == "conflicting_field" and not old["rows"]
    new = table_import.parse(raw, suffix=".csv")
    assert len(new["rows"]) == 1 and new["rows"][0]["trade_no"] == "SYN-T1"


@pytest.mark.parametrize("kind", ["csv", "zip"])
def test_native28_zero_identifier_and_currency_are_literal_source_fields(client, make_case, kind):
    cid = make_case("SYN zero reference and separate currencies")
    rows = [transaction(**{"交易流水号": "0", "凭证号": "SYN-INDEPENDENT", "交易金额": "10.00", "交易币种": "CNY", "交易是否成功": "99"}),
            transaction(**{"交易流水号": "SYN-USD", "交易金额": "2.34", "交易币种": "USD"}),
            transaction(**{"交易流水号": "SYN-EUR", "交易金额": "3.45", "交易币种": "EUR"}),
            transaction(**{"交易流水号": "SYN-NO-CCY", "交易金额": "6.00", "交易币种": ""}),
            transaction(**{"交易流水号": "SYN-NO-DIR", "交易金额": "7.00", "收付标志": ""})]
    raw, name = upload_bytes(rows, kind)
    _, job = add_source(client, cid, raw, name)
    base = f"/api/v1/cases/{cid}"
    flows = client.get(base + "/flows", params={"granularity": "observations"}).json()["items"]
    assert {item["trade_no"]: (item["amount"], item["currency"]) for item in flows} == {
        "0": ("10.00", "CNY"), "SYN-USD": ("2.34", "USD"), "SYN-EUR": ("3.45", "EUR")}
    records = client.get(base + f"/sources/{job['result']['source_id']}/report").json()["items"]
    assert records[0]["details"]["reported_status"] == "unknown"
    assert records[3]["reason_code"] == "invalid_currency"
    assert records[4]["reason_code"] == "missing_direction"
    assert json.loads(records[3]["details"]["source_ledger_evidence"])["row"]["currency"] == ""
    assert client.get(base + "/stats").json()["amount_total"] is None


def test_native28_reciprocal_balance_witnesses_do_not_authorize_posting_pair(client, make_case):
    cid = make_case("SYN native28 has no paired-postings authority")
    rows = [transaction(**{"交易账号": "SYN-A", "交易金额": "0.00", "交易余额": "100.00", "交易流水号": "SYN-BEFORE-A"}),
            transaction(**{"交易账号": "SYN-B", "交易对手账卡号": "SYN-A", "收付标志": "进", "交易金额": "0.00", "交易余额": "0.00", "交易流水号": "SYN-BEFORE-B"}),
            transaction(**{"交易时间": "2026-01-01 11:00:00", "交易账号": "SYN-A", "交易金额": "10.00", "交易余额": "90.00", "交易流水号": "SYN-SHARED"}),
            transaction(**{"交易时间": "2026-01-01 11:00:00", "交易账号": "SYN-B", "交易对手账卡号": "SYN-A", "收付标志": "进", "交易金额": "10.00", "交易余额": "10.00", "交易流水号": "SYN-SHARED"})]
    raw, name = upload_bytes(rows, "zip")
    _, job = add_source(client, cid, raw, name)
    base = f"/api/v1/cases/{cid}"
    assert client.get(base + "/stats").json()["amount_total"] == "20.00"
    records = client.get(base + f"/sources/{job['result']['source_id']}/report").json()["items"]
    assert all("bank_posting_pair_evidence" not in item["details"] for item in records)


@pytest.mark.parametrize("bound", [True, False])
def test_full_closed31_auxiliary_variants_have_explicit_zip_scope(client, make_case, bound):
    cid = make_case("SYN native auxiliary variants")
    path = "SYN-REQUEST/Synthetic bank交易明细信息.csv" if bound else "arbitrary/rows.csv"
    account_headers = next(tuple(sorted(signature)) for signature, role in table_archive._SOURCE_AUXILIARY.items()
                           if role == "account" and len(signature) == 45)
    sub_headers = next(tuple(sorted(signature)) for signature, role in table_archive._SOURCE_AUXILIARY.items()
                       if role == "subaccount" and len(signature) == 13)
    members = [(path, payload([row31()], COLUMNS31)),
               ("SYN-REQUEST/Synthetic bank账户信息.csv", payload([account()], account_headers)),
               ("SYN-REQUEST/Synthetic bank关联子账户信息.csv", payload([{"开户账号": "SYN-A", "子账户账号": "SYN-SUB", "银行名称": "Synthetic bank", "币种": "CNY"}], sub_headers)),
               ("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank"}]))]
    raw = archive(members)
    view, job = add_source(client, cid, raw, "complete.zip")
    assert view["table_summary"]["profile"] == (table_archive.NATIVE31_EVIDENCE_PROFILE if bound else table_archive.PROFILE)
    report = client.get(f"/api/v1/cases/{cid}/sources/{job['result']['source_id']}/report").json()["items"]
    assert len(report) == 4 and sum(item["disposition"] == "auxiliary" for item in report) == 3
    entities = client.get(f"/api/v1/cases/{cid}/resolution").json().get("entities", [])
    if bound:
        flow = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]
        assert any(item["entity_id"] == flow["payer_account"] and item["bank"] == "Synthetic bank" for item in entities)
        assert any(item["kind"] == "subaccount" for item in entities)
    else:
        assert not entities
