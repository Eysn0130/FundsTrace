"""Public import cases for the deterministic two-tier rules."""

import csv
import io
import json

from openpyxl import Workbook
import pytest
import xlwt

from app import table_tiered_headers as tiered
from test_feedback31_reference import COLUMNS, row as bank_record
from test_feedback_import_api import complete


PARENT_A = ("入账", "收款方", "付款方", "账面余额", "本笔发生额", "交易", "辅助")
LEAF_A = ("年月日", "账户标识", "账户标识", "元", "分", "年月日", 1)
DATA_A = ("2026-01-20", "SYN-PAYEE", "SYN-PAYER", "100", "301", "2026-01-07", "")
PARENT_B = ("交易", "本方", "对方", "本笔发生额", "账面余额", "收付", "说明")
LEAF_B = ("年月日", "账户标识", "账户标识", "分", "元", "收付方向", "方向说明")
OUT_FIRST = "本表收付方向0为支出，1为收入"
IN_FIRST = "本表收付方向0为收入，1为支出"


def _payload(rows, suffix, *, padded_direction_row=None, error_cell=None):
    if suffix in (".csv", ".tsv"):
        output = io.StringIO()
        csv.writer(output, delimiter="\t" if suffix == ".tsv" else ",").writerows(rows)
        return output.getvalue().encode()
    output = io.BytesIO()
    if suffix == ".xls":
        book = xlwt.Workbook()
        sheet = book.add_sheet("Synthetic")
        for number, row in enumerate(rows):
            for column, value in enumerate(row):
                sheet.write(number, column, value)
        book.save(output)
    else:
        book = Workbook()
        for row in rows:
            book.active.append(row)
        if padded_direction_row:
            book.active.cell(padded_direction_row, 6).number_format = "00"
        if error_cell:
            book.active[error_cell].data_type = "e"
        book.save(output)
        book.close()
    return output.getvalue()


def _post(client, case_id, rows, *, suffix=".csv", legacy=False, padded_direction_row=None, error_cell=None):
    path = f"/api/import/preview?case_id={case_id}" if legacy else f"/api/v1/cases/{case_id}/imports/preview"
    response = client.post(path, files={"file": (f"synthetic{suffix}", _payload(rows, suffix,
                           padded_direction_row=padded_direction_row, error_cell=error_cell), "application/octet-stream")})
    assert response.status_code == 200, response.text
    return response.json()


def _journal(client, case_id, view):
    result = complete(client, case_id, view)
    assert result["stage"] == "done", result
    base = f"/api/v1/cases/{case_id}/imports/{view['import_id']}"
    response = client.get(f"{base}/records")
    assert response.status_code == 200, response.text
    return response.json()["items"]


@pytest.mark.parametrize(("suffix", "profile"), ((".csv", tiered.PROFILE), (".tsv", tiered.PROFILE),
                                                 (".xlsx", tiered.PROFILE), (".xls", tiered.XLS_PROFILE)))
def test_physical_tiers_keep_roles_units_and_transaction_date(client, make_case, suffix, profile):
    case_id = make_case("Synthetic physical two-tier format")
    row = (*DATA_A[:-1], "=A1" if suffix == ".xlsx" else "辅助值")
    view = _post(client, case_id, [PARENT_A, LEAF_A, row], suffix=suffix)
    assert view["table_summary"]["profile"] == profile
    assert view["table_summary"]["currency_totals"] == {"CNY": "3.01"}
    flow, = view["preview"]
    assert (flow["amount"], flow["balance"], flow["payer_account"], flow["payee_account"], flow["source_row"]) == (
        "3.01", "100.00", "SYN-PAYER", "SYN-PAYEE", 3)
    assert flow["trade_time"].startswith("2026-01-07")
    assert [row["reason_code"] for row in _journal(client, case_id, view)] == [tiered.PARENT_REASON, "transaction_valid"]


@pytest.mark.parametrize("parent", ["本笔发生额（元）", "本笔发生额（厘）", "未知标题"])
def test_unproved_tier_holds_rows_until_a_proved_boundary(client, make_case, parent):
    case_id = make_case("Synthetic denied unit authority")
    broken = (*PARENT_A[:4], parent, *PARENT_A[5:])
    view = _post(client, case_id, [broken, LEAF_A, DATA_A, PARENT_A, LEAF_A, DATA_A])
    assert view["table_summary"]["profile"] == tiered.PROFILE
    assert [row["source_row"] for row in view["preview"]] == [6]
    assert [row["reason_code"] for row in _journal(client, case_id, view)] == [
        tiered.PARENT_REASON, "ambiguous_region_header", tiered.PARENT_REASON, "repeated_header", "transaction_valid"]


def test_currency_conflict_is_not_replaced_by_default_currency(client, make_case):
    case_id = make_case("Synthetic two-tier currency conflict")
    rows = [(*PARENT_A, "结算"), (*LEAF_A[:4], "人民币元", *LEAF_A[5:], "币种"), (*DATA_A, "USD")]
    view = _post(client, case_id, rows)
    assert view["preview"] == []
    assert view["table_summary"]["reason_counts"].get("currency_unit_conflict") == 1


def _movement(date, code, note=""):
    return (date, "SYN-OWN", "SYN-PEER", "301", "100", code, note)


def test_late_codes_are_regional_exact_and_preserve_textual_direction(client, make_case):
    case_id = make_case("Synthetic late direction codebook")
    rows = [PARENT_B, LEAF_B, _movement("2026-01-03", "0"), _movement("2026-01-04", "1", OUT_FIRST),
            _movement("2026-01-05", "01"), PARENT_B, LEAF_B,
            _movement("2026-01-06", "1"), _movement("2026-01-07", "支出")]
    view = _post(client, case_id, rows)
    assert [(row["source_row"], row["payer_account"], row["payee_account"]) for row in view["preview"]] == [
        (3, "SYN-OWN", "SYN-PEER"), (4, "SYN-PEER", "SYN-OWN"), (9, "SYN-OWN", "SYN-PEER")]
    journal = {row["source_row"]: row for row in _journal(client, case_id, view)}
    assert journal[3]["details"][tiered.CODE_FIELD] == "0"
    assert journal[4]["details"][tiered.CODE_FIELD] == "1"
    assert "direction_codebook" not in journal[3]["details"]
    assert "direction_codebook" in journal[4]["details"]
    assert journal[5]["reason_code"] == journal[8]["reason_code"] == "invalid_direction"


def test_conflicting_notes_invalidate_preceding_numeric_directions(client, make_case):
    case_id = make_case("Synthetic competing regional statements")
    rows = [PARENT_B, LEAF_B, _movement("2026-01-03", "0"), _movement("2026-01-04", "1", OUT_FIRST),
            _movement("2026-01-05", "1", IN_FIRST)]
    view = _post(client, case_id, rows)
    assert view["preview"] == []
    journal = {row["source_row"]: row for row in _journal(client, case_id, view)}
    assert [journal[number]["reason_code"] for number in (3, 4, 5)] == ["conflicting_direction"] * 3
    assert journal[3]["details"][tiered.CODE_ISSUE_FIELD] == "conflicting_direction"


def test_workbook_numeric_direction_does_not_erase_a_leading_zero(client, make_case):
    case_id = make_case("Synthetic Excel binary direction format")
    rows = [PARENT_B, LEAF_B, _movement("2026-01-03", 1, OUT_FIRST), _movement("2026-01-04", 1)]
    view = _post(client, case_id, rows, suffix=".xlsx", padded_direction_row=4)
    assert [row["source_row"] for row in view["preview"]] == [3]
    assert _journal(client, case_id, view)[-1]["reason_code"] == "invalid_direction"


def test_core_values_keep_a_dated_row_even_with_header_like_auxiliary_values(client, make_case):
    case_id = make_case("Synthetic unrelated header words")
    header = ("交易日期", "付款方账号", "收款方账号", "交易金额", "A", "B", "C", "D")
    rows = [header, ("2026-01-01", "SYN-A", "SYN-B", "25.00", "交易", "付款方", "收款方", "本笔发生额"),
            ("2026-01-02", "SYN-A", "SYN-B", "12.00", "年月日", "账户标识", "账户标识", "分")]
    view = _post(client, case_id, rows)
    assert [row["amount"] for row in view["preview"]] == ["25.00", "12.00"]
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"


def test_dated_party_records_with_missing_amount_cannot_relabel_later_rows(client, make_case):
    case_id = make_case("Synthetic header words alongside missing amounts")
    rows = [("交易日期", "付款方账号", "收款方账号", "交易金额", "A", "B", "C", "D"),
            ("2026-01-01", "SYN-A", "SYN-B", "1.00", "", "", "", ""),
            ("2026-01-02", "SYN-C", "SYN-D", "", "交易", "收款方", "付款方", "本笔发生额"),
            ("2026-01-03", "SYN-E", "SYN-F", "", "年月日", "账户标识", "账户标识", "分"),
            ("2026-01-04", "SYN-G", "SYN-H", "3.50", "2026-03-05", "SYN-X", "SYN-Y", "5000")]
    view = _post(client, case_id, rows)
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"
    assert [(row["payer_account"], row["payee_account"], row["amount"]) for row in view["preview"]] == [
        ("SYN-A", "SYN-B", "1.00"), ("SYN-G", "SYN-H", "3.50")]
    assert [row["reason_code"] for row in _journal(client, case_id, view)] == [
        "transaction_valid", "missing_amount", "missing_amount", "transaction_valid"]


def test_fullwidth_english_parent_has_the_same_role_authority(client, make_case):
    case_id = make_case("Synthetic fullwidth parent labels")
    parent = ("ＴＲＡＮＳＡＣＴＩＯＮ", "ＰＡＹＥＥ", "ＰＡＹＥＲ", "ＴＲＡＮＳＡＣＴＩＯＮ　ＡＭＯＵＮＴ")
    leaf = ("年月日", "账户标识", "账户标识", "分")
    rows = [("交易日期", "付款方账号", "收款方账号", "交易金额"),
            ("2026-01-03", "SYN-OLD-PAYS", "SYN-OLD-RECEIVES", "1.00"), parent, leaf,
            ("2026-01-04", "SYN-NEW-RECEIVES", "SYN-NEW-PAYS", "301")]
    view = _post(client, case_id, rows, legacy=True)
    assert view["table_summary"]["profile"] == tiered.PROFILE
    flow = view["preview"][-1]
    assert (flow["payer_account"], flow["payee_account"], flow["amount"]) == ("SYN-NEW-PAYS", "SYN-NEW-RECEIVES", "3.01")


def test_manual_preview_cannot_swap_payer_and_payee_proved_by_tiers(client, make_case):
    case_id = make_case("Synthetic frozen party coordinates")
    view = _post(client, case_id, [PARENT_A, LEAF_A, DATA_A])
    mapping = {**view["mapping"], "payer_account": view["mapping"]["payee_account"],
               "payee_account": view["mapping"]["payer_account"]}
    base = f"/api/v1/cases/{case_id}/imports/{view['import_id']}"
    response = client.post(f"{base}/preview", json={"mode": "A", "mapping": mapping})
    assert response.status_code in (400, 422), response.text
    result = complete(client, case_id, view)
    assert result["stage"] == "done", result
    row, = client.get(f"/api/v1/cases/{case_id}/flows").json()["items"]
    assert (row["payer_account"], row["payee_account"], row["amount"]) == ("SYN-PAYER", "SYN-PAYEE", "3.01")


@pytest.mark.parametrize("position", ("D2", "F3", "G3"))
def test_xlsx_error_cells_cannot_prove_units_or_directions(client, make_case, position):
    case_id = make_case("Synthetic XLSX error-cell evidence")
    view = _post(client, case_id, [PARENT_B, LEAF_B, _movement("2026-01-03", "0", OUT_FIRST)],
                 suffix=".xlsx", error_cell=position)
    assert view["preview"] == []
    expected = {"D2": "ambiguous_region_header", "F3": "invalid_cell_type", "G3": "invalid_direction"}
    assert _journal(client, case_id, view)[-1]["reason_code"] == expected[position]


def test_valid_signed_rule_stays_pinned_when_auxiliary_cells_resemble_tiers(client, make_case):
    from app import import_preview_cache

    case_id = make_case("Synthetic signed-rule cold replay")
    note = "本文件交易金额负值表示本方支出，绝对值为元；正值表示本方收入。"
    rows = [("交易日期", "本方账号", "对方账号", "交易金额", "备注", "A", "B", "C", "D"),
            ("2026-01-02", "SYN-OWN", "SYN-PEER", "-12.34", note, "交易", "付款方", "收款方", "本笔发生额"),
            ("2026-01-03", "SYN-OWN", "SYN-PEER", "+2.00", "", "年月日", "账户标识", "账户标识", "分")]
    view = _post(client, case_id, rows)
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"
    assert [(row["payer_account"], row["amount"]) for row in view["preview"]] == [("SYN-OWN", "12.34"), ("SYN-PEER", "2.00")]
    import_preview_cache.discard(view["import_id"])
    request = {"mode": view["mode"], "mapping": view["mapping"]}
    response = client.post(f"/api/v1/cases/{case_id}/imports/{view['import_id']}/preview", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["preview"] == view["preview"]
    import_preview_cache.discard(view["import_id"])
    assert complete(client, case_id, view)["result"]["ok"] == 2


def test_independent_direction_and_an_expense_column_cannot_disagree(client, make_case):
    case_id = make_case("Synthetic direction and expense contradiction")
    rows = [(*PARENT_B, "辅助"), (*LEAF_B, "支出金额(分)"), (*_movement("2026-01-03", "1", OUT_FIRST), "301")]
    view = _post(client, case_id, rows)
    assert view["preview"] == []
    assert _journal(client, case_id, view)[-1]["reason_code"] == "conflicting_direction"


def test_event_profile_requires_the_registered_physical_fields(client, make_case):
    event_labels = ("参考号机构", "参考号渠道", "参考号业务域", "跨账簿事件参考号")
    cases = ((event_labels, tiered.EVIDENCE_PROFILE), ((*event_labels[:-1], "收付方向码表"), tiered.PROFILE))
    for labels, profile in cases:
        case_id = make_case("Synthetic explicit event contract " + profile)
        rows = [(*PARENT_B, "范围", "范围", "范围", "范围"), (*LEAF_B, *labels),
                (*_movement("2026-01-03", "支出"), "SYN-ISSUER", "SYN-CHANNEL", "SYN-SCOPE", "SYN-REFERENCE")]
        view = _post(client, case_id, rows)
        assert view["table_summary"]["profile"] == profile
        details = _journal(client, case_id, view)[-1]["details"]
        assert ("event_reference_evidence" in details) == (profile == tiered.EVIDENCE_PROFILE)


@pytest.mark.parametrize(("suffix", "profile"), ((".csv", tiered.BANK_PROFILE), (".xls", tiered.BANK_XLS_PROFILE)))
def test_registered_bank_variant_preserves_currency_and_numeric_source_status(client, make_case, suffix, profile):
    case_id = make_case("Synthetic registered bank direction codebook")
    records = [bank_record(**{"交易流水号": "SYN-CODE-1", "收付标志": "0"}),
               bank_record(**{"交易流水号": "SYN-CODE-2", "收付标志": "1", "交易是否成功": "01"}),
               bank_record(**{"交易流水号": "SYN-CODE-3", "收付标志": "01", "备注": IN_FIRST})]
    rows = [COLUMNS, *[tuple(record.get(column, "") for column in COLUMNS) for record in records]]
    view = _post(client, case_id, rows, suffix=suffix)
    assert view["table_summary"]["profile"] == profile
    proof = next(item for item in view["table_summary"]["mapping_evidence"] if item["field"] == "direction")
    assert (proof["direction_code_row"], proof["direction_code_column"]) == ("4", str(COLUMNS.index("备注") + 1))
    assert [(row["payer_account"], row["payee_account"], row["amount"], row["currency"]) for row in view["preview"]] == [
        ("SYN-B", "SYN-A", "12.34", "CNY"), ("SYN-A", "SYN-B", "12.34", "CNY")]
    journal = _journal(client, case_id, view)
    assert [entry["reason_code"] for entry in journal] == ["transaction_valid", "transaction_valid", "invalid_direction"]
    assert [entry["details"]["reported_status"] for entry in journal] == ["unknown"] * 3
    assert json.loads(journal[-1]["details"]["source_ledger_evidence"])["source_direction"] == "01"
    exported = client.get(f"/api/cases/{case_id}/export")
    assert exported.is_success, exported.text
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", exported.content)})
    assert restored.is_success, restored.text
