"""Independent synthetic role boundaries; no query-derived transaction owners."""
import csv
from decimal import Decimal
import io

from openpyxl import Workbook
import pytest

from app import table_import as tables
from test_automatic_table_import import preview
from test_table_mapping_versions import profile_preview
from test_table_source_role_lifecycle import transaction, csv_bytes
from test_table_source_roles import ACCOUNT, TRANSACTION_EXPLICIT


def encoded(headers, rows):
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows([headers, *rows])
    return stream.getvalue().encode()


@pytest.mark.parametrize("profile", [f"funds-trace-table-v{n}" for n in range(5, 12)])
def test_new_card_override_is_rejected_by_every_frozen_parser(profile):
    data = encoded(["date", "account", "counterparty account", "direction", "amount", "synthetic card"],
                   [["2026-01-01", "SYN-OWN", "SYN-OTHER", "out", "1.23", "SYN-CARD"]])
    with pytest.raises(tables.TableImportError, match="映射字段"):
        tables.parse(data, suffix=".csv", profile=profile, mapping={"counter_card": "synthetic card"})


def test_old_ready_session_rejects_the_new_role_before_repreview(client, make_case, monkeypatch):
    cid = make_case("Synthetic frozen role vocabulary")
    data = encoded(["date", "account", "counterparty account", "direction", "amount", "synthetic card"],
                   [["2026-01-01", "SYN-OWN", "SYN-OTHER", "out", "1.23", "SYN-CARD"]])
    old = profile_preview(client, cid, data, monkeypatch, "funds-trace-table-v11")
    response = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview", json={
        "mode": old["mode"], "mapping": {**old["mapping"], "counter_card": "synthetic card"}})
    assert response.status_code == 422
    current = preview(client, cid, data)
    response = client.post(f"/api/v1/cases/{cid}/imports/{current['import_id']}/preview", json={
        "mode": current["mode"], "mapping": {**current["mapping"], "counter_card": "synthetic card"}})
    assert response.status_code == 200 and response.json()["table_summary"]["accepted"] == 1


@pytest.mark.parametrize("own_account,counter_account,own,counter", [
    ("SYN-OWN", "SYN-OTHER", "SYN-OWN", "SYN-OTHER"),
    ("", "", "SYN-CARD", "SYN-COUNTER-CARD"),
])
def test_explicit_account_and_card_are_separate_and_only_empty_accounts_use_cards(
        own_account, counter_account, own, counter):
    data = encoded(["date", "本方账号", "本方卡号", "交易对方账号", "交易对方卡号", "direction", "amount"],
                   [["2026-01-01", own_account, "SYN-CARD", counter_account, "SYN-COUNTER-CARD", "out", "1.23"]])
    plan = tables.parse(data, suffix=".csv")
    assert plan["stats"]["accepted"] == 1
    assert (plan["rows"][0]["payer_account"], plan["rows"][0]["payee_account"], plan["rows"][0]["amount"]) == (
        own, counter, Decimal("1.23"))


def test_two_disagreeing_card_candidates_remain_a_conflict_even_with_valid_accounts():
    data = encoded(["date", "本方账号", "本方卡号", "交易卡号", "direction", "amount"],
                   [["2026-01-01", "SYN-OWN", "SYN-CARD-A", "SYN-CARD-B", "out", "1.23"]])
    plan = tables.parse(data, suffix=".csv")
    assert not plan["rows"] and plan["records"][0]["reason_code"] == "conflicting_field"
    assert plan["records"][0]["details"]["counterparty_basis"] == tables.ROLE_CONFLICT_BASIS


@pytest.mark.parametrize("value,reason", [(1234567890123456, "unsafe_account_number"), ("=1+1", "formula_value")])
def test_unused_counter_card_still_passes_critical_identity_validation(value, reason):
    workbook = Workbook()
    workbook.active.append(["date", "account", "counterparty account", "交易对方卡号", "direction", "amount"])
    workbook.active.append(["2026-01-01", "SYN-OWN", "SYN-OTHER", value, "out", "1.23"])
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    plan = tables.parse(stream.getvalue(), suffix=".xlsx")
    assert not plan["rows"] and reason in plan["records"][0]["details"]["validation_issues"].split(",")


@pytest.mark.parametrize("change", ["extra", "missing", "duplicate"])
def test_reference_role_policy_requires_the_complete_closed_template(change):
    headers = list(TRANSACTION_EXPLICIT)
    if change == "extra":
        headers.append("undefined source column")
    elif change == "missing":
        headers.remove("商户名称")
    else:
        headers.append("交易流水号")
    plan = tables.parse(csv_bytes(headers, [transaction()]), suffix=".csv")
    assert not plan["rows"]
    assert "conflicting_field" in plan["records"][0]["details"]["validation_issues"].split(",")
    assert "trade_no_basis" not in plan["records"][0]["details"]


def test_auxiliary_unnamed_column_data_is_preserved_as_a_source_error():
    headers = [*ACCOUNT, ""]
    plan = tables.parse(encoded(headers, [[*["" for _ in ACCOUNT], "SYN-UNDECLARED"]]), suffix=".csv")
    assert plan["members"][0]["role"] == "auxiliary" and not plan["rows"]
    assert plan["records"][0]["reason_code"] == "column_count_mismatch"
    assert plan["records"][0]["details"]["source_role"] == "account"
