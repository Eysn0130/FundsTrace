"""Regressions for the complete 39-column case-investigation export.

That cataloged layout carries both 交易流水号 and 凭证号. The voucher is a
retained source fact, not a rival value of the transaction-id field, so those
rows must be accepted. A near-miss layout (one label missing, one extra label)
must keep the conservative conflicting-field path.
"""
import csv
import io

from app import table_import as tables


COLUMNS = (
    "交易卡号", "交易账号", "主户名", "开户人证件号码", "交易日期", "交易时间", "交易净额",
    "交易金额", "交易余额", "收付标志", "交易对手账卡号", "现金标志", "对手户名", "是否调取",
    "已调取账号对手身份", "对手身份证号", "对手开户银行", "摘要说明", "交易币种", "交易网点名称",
    "交易发生地", "交易是否成功", "传票号", "IP地址", "MAC地址", "对手交易余额", "交易流水号",
    "日志号", "凭证种类", "凭证号", "交易柜员号", "备注", "商户名称", "交易类型",
    "查询反馈结果原因", "备用字段1", "备用字段2", "备用字段3", "重点字样",
)


def source_row(**changes):
    values = {
        "交易卡号": "SYN-CARD-1", "交易账号": "SYN-OWN-1", "主户名": "Synthetic own",
        "交易日期": "2026-09-01", "交易时间": "2026-09-01 10:00:00", "交易净额": "-100.00",
        "交易金额": "100.00", "交易余额": "900.00", "收付标志": "出",
        "交易对手账卡号": "SYN-COUNTER-1", "现金标志": "00", "对手户名": "Synthetic counter",
        "是否调取": "未调取", "对手开户银行": "Synthetic bank", "摘要说明": "Synthetic transfer",
        "交易币种": "CNY", "交易网点名称": "Synthetic branch", "交易发生地": "在线",
        "交易是否成功": "01", "交易流水号": "SYN-TRANSACTION", "凭证种类": "4001",
        "凭证号": "SYN-CARD-1", "交易类型": "转账",
    }
    values.update(changes)
    return values


def payload(rows, headers=COLUMNS):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows([[row.get(column, "") for column in headers] for row in rows])
    return output.getvalue().encode("utf-8")


def parse(rows, headers=COLUMNS, **options):
    return tables.parse(payload(rows, headers), suffix=".csv", **options)


def test_complete_template_keeps_voucher_apart_from_transaction_id():
    plan = parse([source_row()])
    assert plan["profile"] == tables.PROFILE
    assert [record["disposition"] for record in plan["records"]] == ["accepted"]
    assert plan["stats"]["accepted"] == 1 and plan["stats"]["unresolved"] == 0
    assert plan["rows"][0]["trade_no"] == "SYN-TRANSACTION"
    details = plan["records"][0]["details"]
    assert details["trade_no_basis"] == "source_transaction_id"
    evidence = {entry["column"]: entry for entry in plan["mapping_evidence"]}
    assert evidence["交易流水号"]["basis"] == "source_transaction_id"
    assert evidence["凭证号"]["field"] == "voucher_reference"
    assert evidence["凭证号"]["basis"] == "independent_voucher_reference"
    assert plan["mapping"]["trade_no"] == "交易流水号"


def test_blank_transaction_id_keeps_voucher_fallback():
    plan = parse([source_row(**{"交易流水号": ""})])
    assert plan["stats"]["accepted"] == 1 and plan["stats"]["unresolved"] == 0
    assert plan["rows"][0]["trade_no"] == "SYN-CARD-1"
    assert plan["records"][0]["details"]["trade_no_basis"] == "legacy_voucher_fallback"


def test_equal_transaction_id_and_voucher_stay_accepted():
    plan = parse([source_row(**{"凭证号": "SYN-TRANSACTION"})])
    assert plan["rows"][0]["trade_no"] == "SYN-TRANSACTION"
    assert plan["stats"]["unresolved"] == 0


def test_missing_one_label_stays_conservative():
    headers = tuple(column for column in COLUMNS if column != "重点字样")
    plan = parse([source_row()], headers=headers)
    assert [record["reason_code"] for record in plan["records"]] == ["conflicting_field"]
    assert not plan["rows"]


def test_extra_label_stays_conservative():
    headers = COLUMNS + ("交易单号",)
    plan = parse([source_row(**{"交易单号": "SYN-OTHER"})], headers=headers)
    assert [record["reason_code"] for record in plan["records"]] == ["conflicting_field"]
    assert not plan["rows"]


def test_duplicate_label_stays_conservative():
    headers = COLUMNS + ("凭证号",)
    plan = parse([source_row()], headers=headers)
    assert not plan["rows"]


def test_explicit_reference_policy_resolves_a_near_miss_layout():
    headers = tuple(column for column in COLUMNS if column != "重点字样")
    assert not parse([source_row()], headers=headers)["rows"]
    plan = parse([source_row()], headers=headers, voucher_reference=True)
    assert plan["rows"][0]["trade_no"] == "SYN-TRANSACTION"
    assert plan["records"][0]["details"]["trade_no_basis"] == "source_transaction_id"
