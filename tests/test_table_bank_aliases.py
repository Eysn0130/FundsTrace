"""Synthetic v13 bank-name recognition preserves the frozen source roles."""
import csv
from decimal import Decimal
import io

import pytest
from openpyxl import Workbook

from app import table_import as tables


def payload(headers, rows, suffix=".csv"):
    output = io.BytesIO()
    if suffix == ".xlsx":
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        workbook.save(output)
        workbook.close()
    else:
        text = io.StringIO(newline="")
        csv.writer(text).writerows([headers, *rows])
        output.write(text.getvalue().encode())
    return output.getvalue()


def parse(headers, rows, suffix=".csv", *, profile=None):
    options = {} if profile is None else {"profile": profile}
    return tables.parse(payload(headers, rows, suffix), suffix=suffix, **options)


@pytest.mark.parametrize("direction,explicit_bank", [("out", "payee_bank"), ("in", "payer_bank")])
def test_exact_source_bank_is_not_ignored_when_an_explicit_party_bank_conflicts(direction, explicit_bank):
    result = parse(["date", "本方卡号", "交易对方账号", "交易对方账号开户行", explicit_bank, "direction", "amount"],
                   [["2026-01-01", "SYN-CARD", "SYN-OTHER", "Synthetic source bank", "Synthetic different bank", direction, "1.23"]])
    assert not result["rows"]
    assert result["records"][0]["reason_code"] == "conflicting_field"


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("direction,counter_side", [("out", "payee"), ("in", "payer")])
@pytest.mark.parametrize("own_header", ["本方账号", "本方卡号"])
@pytest.mark.parametrize("reverse", [False, True])
def test_exact_bank_column_is_automatically_mapped_to_its_directed_counterparty(
        suffix, direction, counter_side, own_header, reverse):
    headers = ["date", own_header, "交易对方账号", "交易对方账号开户行", "direction", "amount"]
    row = ["2026-01-01", "SYN-OWN", "SYN-OTHER", "Synthetic source bank", direction, "1.23"]
    if reverse:
        headers, row = list(reversed(headers)), list(reversed(row))
    result = parse(headers, [row], suffix)
    assert result["profile"] == "funds-trace-table-v16" and result["stats"]["accepted"] == 1
    flow = result["rows"][0]
    assert flow[counter_side + "_account"] == "SYN-OTHER"
    assert flow[counter_side + "_bank"] == "Synthetic source bank"
    assert flow[("payer" if counter_side == "payee" else "payee") + "_bank"] == ""
    assert flow["amount"] == Decimal("1.23") and flow["self_account"] == "SYN-OWN"
    assert result["mapping"]["counter_bank"] == "交易对方账号开户行"
    evidence = [item for item in result["mapping_evidence"] if item["field"] == "counter_bank"]
    assert len(evidence) == 1 and evidence[0]["column"] == "交易对方账号开户行"
    assert evidence[0]["column_index"] == headers.index("交易对方账号开户行") + 1
    assert evidence[0]["header_row"] == 1 and evidence[0]["member_path"] == result["records"][0]["member_path"]
    assert result["records"][0]["source_row"] == 2
    assert result["documents"][0]["mapping"]["counter_bank"] == "交易对方账号开户行"


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("direction,counter_bank", [("out", "payee_bank"), ("in", "payer_bank")])
@pytest.mark.parametrize("same", [False, True])
def test_duplicate_bank_candidates_all_validate_and_do_not_depend_on_column_order(suffix, direction, counter_bank, same):
    headers = ["date", "本方卡号", "交易对方账号", "交易对方账号开户行", "对手开户银行", counter_bank, "direction", "amount"]
    row = ["2026-01-01", "SYN-CARD", "SYN-OTHER", "Synthetic source bank",
           "Synthetic source bank" if same else "Synthetic different bank", "Synthetic source bank", direction, "1.23"]
    for reversed_order in (False, True):
        result = parse(list(reversed(headers)) if reversed_order else headers,
                       [list(reversed(row)) if reversed_order else row], suffix)
        assert result["stats"]["accepted"] == int(same)
        if same:
            assert result["rows"][0][counter_bank] == "Synthetic source bank"
        else:
            assert result["records"][0]["reason_code"] == "conflicting_field"


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("source_bank,accepted", [("Synthetic payee bank", True), ("Synthetic unrelated bank", False)])
def test_complete_parties_without_direction_validate_bank_in_the_same_account_role(suffix, source_bank, accepted):
    result = parse(["date", "本方卡号", "交易对方账号", "payer_account", "payee_account",
                    "交易对方账号开户行", "payer_bank", "payee_bank", "amount"],
                   [["2026-01-01", "SYN-CARD", "SYN-PAYEE", "SYN-PAYER", "SYN-PAYEE", source_bank,
                     "Synthetic payer bank", "Synthetic payee bank", "1.23"]], suffix)
    assert result["stats"]["accepted"] == int(accepted)
    if accepted:
        assert (result["rows"][0]["payer_account"], result["rows"][0]["payee_account"], result["rows"][0]["self_account"]) == (
            "SYN-PAYER", "SYN-PAYEE", "SYN-CARD")
    else:
        assert result["records"][0]["reason_code"] == "conflicting_field"


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("own_header", ["本方账号", "本方卡号"])
def test_source_bank_never_supplies_a_missing_ledger_direction(suffix, own_header):
    result = parse(["date", own_header, "交易对方账号", "交易对方账号开户行", "amount"],
                   [["2026-01-01", "SYN-OWN", "SYN-OTHER", "Synthetic source bank", "1.23"]], suffix)
    assert not result["rows"] and result["records"][0]["reason_code"] == "missing_direction"


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("same", [False, True])
def test_same_entry_balance_proof_keeps_bank_role_validation_without_an_explicit_direction(suffix, same):
    result = parse(["date", "本方卡号", "交易对方账号", "交易对方账号开户行", "payee_bank", "amount",
                    "交易前账面余额", "交易后账面余额"],
                   [["2026-01-01", "SYN-CARD", "SYN-OTHER", "Synthetic source bank",
                     "Synthetic source bank" if same else "Synthetic different bank", "1.23", "100", "98.77"]], suffix)
    assert result["stats"]["accepted"] == int(same)
    if same:
        assert (result["rows"][0]["payer_account"], result["rows"][0]["payee_bank"]) == (
            "SYN-CARD", "Synthetic source bank")
        assert result["records"][0]["details"]["direction_basis"] == "same_entry_book_balances"
    else:
        assert result["records"][0]["reason_code"] == "conflicting_field"


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("direction,counter_bank", [("out", "payee_bank"), ("in", "payer_bank")])
@pytest.mark.parametrize("explicit_value", ["", "Synthetic explicit bank"])
def test_empty_source_bank_stays_empty_or_preserves_an_explicit_party_bank(suffix, direction, counter_bank, explicit_value):
    result = parse(["date", "本方账号", "交易对方账号", "交易对方账号开户行", counter_bank, "direction", "amount"],
                   [["2026-01-01", "SYN-OWN", "SYN-OTHER", "", explicit_value, direction, "1.23"]], suffix)
    assert result["stats"]["accepted"] == 1 and result["rows"][0][counter_bank] == explicit_value


def test_newly_recognized_bank_formula_is_a_critical_unexecuted_cell():
    headers = ["date", "本方卡号", "交易对方账号", "交易对方账号开户行", "payee_bank", "direction", "amount"]
    rows = [["2026-01-01", "SYN-CARD", "SYN-OTHER", "=1+1", "Synthetic explicit bank", "out", "1.23"]]
    result = parse(headers, rows, ".xlsx")
    assert not result["rows"] and result["records"][0]["reason_code"] == "formula_value"
    frozen = parse(headers, rows, ".xlsx", profile="funds-trace-table-v12")
    assert frozen["stats"]["accepted"] == 1


@pytest.mark.parametrize("own_header", ["本方账号", "本方卡号"])
def test_bank_recognition_cannot_hide_an_unsafe_numeric_identifier(own_header):
    result = parse(["date", own_header, "交易对方账号", "交易对方账号开户行", "direction", "amount"],
                   [["2026-01-01", 1234567890123456, "SYN-OTHER", "Synthetic source bank", "out", "1.23"]], ".xlsx")
    assert not result["rows"]
    assert "unsafe_account_number" in result["records"][0]["details"]["validation_issues"].split(",")


@pytest.mark.parametrize("version", range(5, 13))
@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_all_old_profiles_keep_the_unknown_bank_header_and_original_party_bank_behavior(version, suffix):
    headers = ["date", "本方账号", "交易对方账号", "交易对方账号开户行", "payee_bank", "direction", "amount"]
    result = parse(headers, [["2026-01-01", "SYN-OWN", "SYN-OTHER", "Synthetic source bank",
                              "Synthetic explicit bank", "out", "1.23"]], suffix, profile=f"funds-trace-table-v{version}")
    assert result["stats"]["accepted"] == 1
    assert result["rows"][0]["payee_bank"] == ("" if version == 5 else "Synthetic explicit bank")
    assert not any(item["field"] == "counter_bank" for item in result["mapping_evidence"])
    only_source = parse(headers[:4] + headers[5:], [["2026-01-01", "SYN-OWN", "SYN-OTHER",
                                                   "Synthetic source bank", "out", "1.23"]], suffix,
                        profile=f"funds-trace-table-v{version}")
    assert only_source["stats"]["accepted"] == 1 and only_source["rows"][0]["payee_bank"] == ""


@pytest.mark.parametrize("header", ["原交易对方账号开户行", "交易对方账号开户行说明", "交易对方账号开户行(估计)", "交易对方账号开户行(元)"])
def test_unknown_bank_header_extensions_do_not_gain_authority(header):
    result = parse(["date", "本方账号", "交易对方账号", header, "direction", "amount"],
                   [["2026-01-01", "SYN-OWN", "SYN-OTHER", "Synthetic unsupported bank", "out", "1.23"]])
    assert result["stats"]["accepted"] == 1 and result["rows"][0]["payee_bank"] == ""
    assert not any(item["field"] == "counter_bank" for item in result["mapping_evidence"])


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_bank_conflict_after_the_first_hundred_rows_is_still_validated(suffix):
    headers = ["date", "本方账号", "交易对方账号", "交易对方账号开户行", "payee_bank", "direction", "amount"]
    valid = ["2026-01-01", "SYN-OWN", "SYN-OTHER", "Synthetic source bank", "Synthetic source bank", "out", "1.23"]
    bad = [*valid]
    bad[4] = "Synthetic different bank"
    result = parse(headers, [valid for _ in range(105)] + [bad], suffix)
    assert result["stats"]["accepted"] == 105 and result["stats"]["unresolved"] == 1
    assert result["records"][-1]["source_row"] == 107
    assert result["records"][-1]["reason_code"] == "conflicting_field"
    assert result["stats"]["currency_totals"] == {"CNY": "129.15"}


@pytest.mark.parametrize("profile", ["funds-trace-table-v13", "funds-trace-table-v14", "funds-trace-table-v15", "funds-trace-table-v16"])
@pytest.mark.parametrize("direction,own_party,party_card", [("out", "payer_account", "付款人卡号"), ("in", "payee_account", "收款人卡号")])
def test_bank_profiles_inherit_v12_card_kind_constraints_without_cross_kind_comparison(profile, direction, own_party, party_card):
    headers = ["date", "本方账号", "本方卡号", "交易对方账号", "交易对方账号开户行", own_party, party_card, "direction", "amount"]
    rows = [["2026-01-01", "SYN-OWN-ACCOUNT", "SYN-OWN-CARD", "SYN-OTHER", "Synthetic source bank",
             "SYN-OWN-ACCOUNT", "SYN-OWN-CARD", direction, "1.23"]]
    result = parse(headers, rows, profile=profile)
    assert result["stats"]["accepted"] == 1
    assert result["rows"][0][own_party] == result["rows"][0]["self_account"] == "SYN-OWN-ACCOUNT"
    rows[0][6] = "SYN-DIFFERENT-CARD"
    rejected = parse(headers, rows, profile=profile)
    assert not rejected["rows"] and rejected["records"][0]["reason_code"] == "conflicting_field"
