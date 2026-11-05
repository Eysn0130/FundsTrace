"""Exact observed uniform-37 reference roles; query annotations stay annotations."""
import io
import zipfile

import pytest

from app import table_import
from test_feedback31_reference import payload
from test_folder_evidence import accepted, account, table


COLUMNS = tuple("姓名(查询条件) 证件号码(查询条件) 查询账号 查询卡号 交易类型 借贷标志 币种 交易金额 交易余额 交易时间 交易流水号 交易对方名称 交易对方账号 交易对方卡号 交易对方证件号码 交易对手余额 交易对方账号开户行 交易摘要 交易网点名称 交易网点代码 日志号 传票号 凭证种类 凭证号 终端号 交易是否成功 交易发生地 商户名称 商户号 IP地址 MAC地址 交易柜员号 备注 本方账号 本方卡号".split()) + ("Unnamed: 35", "Unnamed: 36")
SECOND_ORDER = ("查询账号", "姓名(查询条件)", "证件号码(查询条件)") + COLUMNS[3:]


def row(**changes):
    return {"姓名(查询条件)": "Synthetic query annotation", "证件号码(查询条件)": "SYN-QUERY-DOCUMENT",
            "查询账号": "SYN-A", "查询卡号": "SYN-C1", "本方账号": "SYN-A", "本方卡号": "SYN-C1",
            "交易时间": "2026-01-01 10:00:00.123456", "交易金额": "12.34", "交易余额": "222.34",
            "借贷标志": "出", "币种": "CNY", "交易流水号": "SYN-PRIMARY", "凭证号": "SYN-VOUCHER",
            "交易是否成功": "1", "交易对方账号": "SYN-B", "交易对方卡号": "SYN-CB",
            "交易对方名称": "Synthetic counterparty", "交易对方账号开户行": "Synthetic other bank", **changes}


@pytest.mark.parametrize("headers", [COLUMNS, SECOND_ORDER])
def test_public_exact37_orders_separate_reference_roles_and_keep_originals(client, make_case, headers):
    cid = make_case("Synthetic observed uniform reference contract")
    raw = payload([row()], headers)
    view, receipt = accepted(client, cid, [("rows.csv", raw)], scope_bank=None)
    assert view["stats"]["ok"] == receipt["ok"] == 1
    flow = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]
    assert flow["trade_no"] == "SYN-PRIMARY" and flow["raw_self_account"] == "SYN-A" and flow["amount"] == "12.34"
    roles = {item["column"]: item for item in view["table_summary"]["mapping_evidence"] if item["column"] in {"交易流水号", "凭证号"}}
    assert roles["交易流水号"]["basis"] == "source_transaction_id" and roles["凭证号"]["basis"] == "independent_voucher_reference"
    assert int(roles["凭证号"]["column_index"]) == headers.index("凭证号") + 1
    original = client.get(f"/api/v1/cases/{cid}/sources/{receipt['source_id']}/file")
    with zipfile.ZipFile(io.BytesIO(original.content)) as source:
        assert source.read("rows.csv") == raw
    package = client.get(f"/api/cases/{cid}/export").content
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package)})
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/v1/cases/{restored.json()['case_id']}/flows").json()["items"][0]["trade_no"] == "SYN-PRIMARY"


@pytest.mark.parametrize("headers", [COLUMNS[:-1], COLUMNS + ("extra",),
    tuple("交易流水号" if name == "备注" else name for name in COLUMNS),
    tuple("Unnamed:35" if name == "Unnamed: 35" else name for name in COLUMNS)])
def test_incomplete_extra_duplicate_or_similar37_headers_do_not_inherit_contract(client, make_case, headers):
    _, receipt = accepted(client, make_case("Synthetic unknown uniform layout"), [("rows.csv", payload([row()], headers))], scope_bank=None)
    assert receipt["ok"] == 0 and receipt["table_summary"]["unresolved"] == 1


@pytest.mark.parametrize("changes", [{"Unnamed: 35": "SYN-UNKNOWN-CELL"}, {"Unnamed: 36": "0"},
    {"交易金额": "12.345"}, {"币种": "unknown"}, {"借贷标志": "unknown"}, {"交易是否成功": "unknown"}])
def test_unknown_padding_and_invalid_financial_values_are_not_admitted(client, make_case, changes):
    _, receipt = accepted(client, make_case("Synthetic uniform financial controls"), [("rows.csv", payload([row(**changes)], COLUMNS))], scope_bank=None)
    assert receipt["ok"] == 0 and receipt["table_summary"]["unresolved"] == 1


def test_query_only_owner_does_not_gain_an_identity_from_reference_contract(client, make_case):
    _, receipt = accepted(client, make_case("Synthetic query only remains unresolved"), [("rows.csv", payload([
        row(**{"本方账号": "", "本方卡号": "", "查询账号": "SYN-QUERY-ONLY", "查询卡号": "SYN-QUERY-CARD"})], COLUMNS))], scope_bank=None)
    assert receipt["ok"] == 0 and receipt["table_summary"]["unresolved"] == 1


def test_reference37_does_not_inherit31_institution_or_select_a_query_ledger(client, make_case):
    cid = make_case("Synthetic reference role is not an institution contract")
    _, receipt = accepted(client, cid, [("SYN-REQUEST/Synthetic bank交易明细信息.csv", payload([
        row(**{"本方账号": "", "查询账号": "SYN-QUERY-PARENT"})], COLUMNS)),
        ("accounts.csv", table("account", [account()])),
        ("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank"}]))], scope_bank=None)
    assert receipt["ok"] == 1
    flow = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]
    assert flow["raw_self_account"] == "SYN-C1"
    own = next(item for item in client.get(f"/api/v1/cases/{cid}/resolution").json()["entities"] if item["entity_id"] == flow["payer_account"])
    assert own["kind"] == "card" and own["bank"] == "" and own["parent_entity_id"] is None


def test_old_profile_keeps37_reference_conflict():
    plan = table_import.parse(payload([row()], COLUMNS), suffix=".csv", profile=table_import.CURRENCY_REGION_PROFILE,
                              reference_policy=table_import.TRANSACTION_REFERENCE_POLICY)
    assert not plan["rows"] and plan["records"][0]["reason_code"] == "conflicting_field"


@pytest.mark.parametrize("column,value", [("transaction id", "SYN-DIFFERENT"), ("amount", "19.99"),
                                        ("收支方向", "入"), ("transaction time", "2026-01-02 10:00:00")])
def test_extra_same_role_candidates_cannot_be_hidden_by37_reference_roles(client, make_case, column, value):
    _, receipt = accepted(client, make_case("Synthetic uniform same-role conflict"), [("rows.csv", payload([
        row(**{"交易流水号": "SYN-SAME", "凭证号": "SYN-SAME", column: value})], COLUMNS + (column,)))], scope_bank=None)
    assert receipt["ok"] == 0 and receipt["table_summary"]["unresolved"] == 1
