"""Closed new-profile reference roles for the observed 31-column family."""
import csv
import io
import json
import zipfile

from openpyxl import Workbook
import pytest

from app import table_archive, table_import
from test_folder_evidence import accepted, account, table


COLUMNS = tuple("交易卡号 交易账号 交易方户名 交易方证件号码 交易时间 交易金额 交易余额 收付标志 交易对手账卡号 现金标志 对手户名 对手身份证号 对手开户银行 摘要说明 交易币种 交易网点名称 交易发生地 交易是否成功 传票号 IP地址 MAC地址 对手交易余额 交易流水号 日志号 凭证种类 凭证号 交易柜员号 备注 商户名称 交易类型 查询反馈结果原因".split())


def row(**changes):
    return {"交易账号": "SYN-A", "交易卡号": "SYN-C1", "交易方户名": "Synthetic own source name",
            "交易方证件号码": "SYN-SOURCE-DOCUMENT", "交易时间": "2026-01-01 10:00:00.123456",
            "交易金额": "12.34", "交易余额": "9999999999999999.99", "收付标志": "出",
            "交易币种": "CNY", "交易对手账卡号": "SYN-B", "对手开户银行": "Synthetic other bank",
            "交易是否成功": "1", "交易流水号": "SYN-TRANSACTION", "凭证号": "SYN-VOUCHER", **changes}


def payload(records, headers=COLUMNS, suffix="csv"):
    if suffix == "xlsx":
        workbook = Workbook()
        workbook.active.title = "Synthetic records"
        workbook.active.append(list(headers))
        for record in records:
            workbook.active.append([record.get(column, "") for column in headers])
        stream = io.BytesIO()
        workbook.save(stream)
        return stream.getvalue()
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows([[record.get(column, "") for column in headers] for record in records])
    return stream.getvalue().encode("utf-8-sig")


@pytest.mark.parametrize("zipped,suffix,reordered", [(False, "csv", False), (True, "csv", True), (False, "xlsx", True)])
def test_public_closed31_references_preserve_both_source_columns_and_portable_primary(client, make_case, zipped, suffix, reordered):
    cid = make_case("Synthetic closed reference roles")
    headers = tuple(reversed(COLUMNS)) if reordered else COLUMNS
    raw = payload([row()], headers, suffix)
    path = f"SYN-REQUEST/Synthetic bank交易明细信息.{suffix}"
    members = [(path, raw), ("accounts.csv", table("account", [account()])),
               ("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank"}]))]
    view, receipt = accepted(client, cid, members, zipped=zipped, scope_bank=None)
    assert view["stats"]["ok"] == receipt["ok"] == 1
    flow = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]
    assert flow["trade_no"] == "SYN-TRANSACTION" and flow["amount"] == "12.34"
    assert flow["balance"] == "9999999999999999.99" and flow["raw_self_account"] == "SYN-A"
    evidence = {item["column"]: item for item in view["table_summary"]["mapping_evidence"] if item["column"] in {"交易流水号", "凭证号"}}
    assert evidence["交易流水号"]["basis"] == "source_transaction_id"
    assert evidence["凭证号"]["field"] == "voucher_reference" and evidence["凭证号"]["basis"] == "independent_voucher_reference"
    assert int(evidence["凭证号"]["column_index"]) == headers.index("凭证号") + 1
    report = client.get(f"/api/v1/cases/{cid}/sources/{receipt['source_id']}/report").json()["items"]
    imported = next(item for item in report if item["disposition"] == "imported")
    assert imported["details"]["trade_no_basis"] == "source_transaction_id"
    original = client.get(f"/api/v1/cases/{cid}/sources/{receipt['source_id']}/file")
    assert original.status_code == 200
    with zipfile.ZipFile(io.BytesIO(original.content)) as source:
        assert source.read(path) == raw
    exported = client.get(f"/api/cases/{cid}/export").content
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", exported)})
    assert restored.status_code == 200, restored.text
    after = client.get(f"/api/v1/cases/{restored.json()['case_id']}/flows").json()["items"][0]
    assert after["trade_no"] == flow["trade_no"] and after["amount"] == flow["amount"] and after["balance"] == flow["balance"]


def test_historical_profiles_keep_closed31_reference_conflict():
    raw = payload([row()])
    for profile in (table_import.CURRENCY_REGION_PROFILE, table_import.IDENTITY_PROFILE):
        plan = table_import.parse(raw, suffix=".csv", profile=profile, reference_policy=table_import.TRANSACTION_REFERENCE_POLICY)
        assert not plan["rows"] and plan["records"][0]["reason_code"] == "conflicting_field"


@pytest.mark.parametrize("column,value", [("amount", "19.99"), ("收支方向", "入"), ("transaction time", "2026-01-02 10:00:00"), ("transaction id", "SYN-OTHER")])
def test_reference_contract_does_not_suppress_same_role_or_financial_conflicts(client, make_case, column, value):
    cid = make_case("Synthetic reference conflict control")
    raw = payload([row(**{"交易流水号": "SYN-SAME", "凭证号": "SYN-SAME", column: value})], COLUMNS + (column,))
    view, receipt = accepted(client, cid, [("rows.csv", raw)], scope_bank=None)
    assert view["stats"]["ok"] == receipt["ok"] == 0
    assert receipt["table_summary"]["unresolved"] == 1


@pytest.mark.parametrize("bound", [True, False])
def test_closed31_institution_requires_existing_manifest_request_and_role_binding(client, make_case, bound):
    cid = make_case("Synthetic bounded institution contract")
    path = "SYN-REQUEST/Synthetic bank交易明细信息.csv" if bound else "arbitrary/Synthetic bank rows.csv"
    _, receipt = accepted(client, cid, [(path, payload([row()])), ("accounts.csv", table("account", [account()])),
        ("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank"}]))], scope_bank=None)
    assert receipt["ok"] == 1
    flow = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]
    own = next(item for item in client.get(f"/api/v1/cases/{cid}/resolution").json()["entities"] if item["entity_id"] == flow["payer_account"])
    assert own["bank"] == ("Synthetic bank" if bound else "")
    assert own["status"] == ("resolved" if bound else "unscoped")


@pytest.mark.parametrize("primary,voucher,expected,basis", [
    ("--", "SYN-VOUCHER", "SYN-VOUCHER", "legacy_voucher_fallback"),
    ("SYN-TRANSACTION", "N/A", "SYN-TRANSACTION", "source_transaction_id"),
    ("--", "--", "", "none"),
    ("000000", "SYN-VOUCHER", "000000", "source_transaction_id"),
])
def test_closed31_missing_references_use_their_own_roles_without_editing_source(client, make_case, primary, voucher, expected, basis):
    cid = make_case("Synthetic reference missing values")
    raw = payload([row(**{"交易流水号": primary, "凭证号": voucher})])
    _, receipt = accepted(client, cid, [("rows.csv", raw)], scope_bank=None)
    assert receipt["ok"] == 1
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]["trade_no"] == expected
    report = client.get(f"/api/v1/cases/{cid}/sources/{receipt['source_id']}/report").json()["items"]
    assert next(item for item in report if item["disposition"] == "imported")["details"]["trade_no_basis"] == basis
    original = client.get(f"/api/v1/cases/{cid}/sources/{receipt['source_id']}/file")
    with zipfile.ZipFile(io.BytesIO(original.content)) as source:
        assert source.read("rows.csv") == raw


@pytest.mark.parametrize("headers", [COLUMNS[:-1], COLUMNS + ("unknown",),
    tuple("unknown" if name == "商户名称" else name for name in COLUMNS),
    tuple("交易流水号" if name == "商户名称" else name for name in COLUMNS)])
def test_incomplete_unknown_or_duplicate31_headers_do_not_inherit_reference_contract(client, make_case, headers):
    cid = make_case("Synthetic unknown reference family")
    _, receipt = accepted(client, cid, [("rows.csv", payload([row()], headers))], scope_bank=None)
    assert receipt["ok"] == 0 and receipt["table_summary"]["unresolved"] == 1


@pytest.mark.parametrize("changes,disposition", [({"交易金额": "0"}, "non_monetary"),
    ({"交易金额": "-12.34"}, "unresolved"), ({"交易金额": "12.345"}, "unresolved"),
    ({"收付标志": "unknown"}, "unresolved"), ({"交易是否成功": "unknown"}, "unresolved"),
    ({"交易币种": "unknown"}, "unresolved"), ({"交易时间": "unknown"}, "unresolved")])
def test_reference_roles_never_relax_financial_admission(client, make_case, changes, disposition):
    cid = make_case("Synthetic reference financial controls")
    _, receipt = accepted(client, cid, [("rows.csv", payload([row(**changes)]))], scope_bank=None)
    assert receipt["ok"] == 0 and receipt["table_summary"][disposition] == 1


@pytest.mark.parametrize("gap", ["request", "institution", "auxiliary_conflict"])
def test_closed31_scope_does_not_cross_wrong_request_institution_or_auxiliary_claim(client, make_case, gap):
    cid = make_case("Synthetic institution scope controls")
    manifest = {"请求单号": "SYN-OTHER-REQUEST" if gap == "request" else "SYN-REQUEST",
                "银行名称": "Different institution" if gap == "institution" else "Synthetic bank"}
    aux = account(bank="Different institution" if gap == "auxiliary_conflict" else "Synthetic bank")
    _, receipt = accepted(client, cid, [("SYN-REQUEST/Synthetic bank交易明细信息.csv", payload([row()])),
        ("SYN-REQUEST/Synthetic bank账户信息.csv", table("account", [aux])),
        ("manifest.csv", table("task", [manifest]))], scope_bank=None)
    assert receipt["ok"] == 1
    flow = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]
    registry = client.get(f"/api/v1/cases/{cid}/resolution").json()["entities"]
    own = next(item for item in registry if item["entity_id"] == flow["payer_account"])
    assert own["bank"] == "" and own["status"] == "unscoped"
    if gap == "auxiliary_conflict":
        assert receipt["resolution"]["conflicts"] and any(item["status"] == "conflict" for item in registry)
