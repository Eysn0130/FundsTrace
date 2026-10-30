"""Archive v4 enables only the two independently reproduced header fixes."""
import csv
from decimal import Decimal
import hashlib
import io
import zipfile

import pytest

from app import table_archive, table_import


V4 = "funds-trace-table-archive-v4"
V3 = "funds-trace-table-archive-v3"


def csv_bytes(rows):
    output = io.StringIO(newline="")
    csv.writer(output).writerows(rows)
    return output.getvalue().encode("utf-8")


def envelope(members):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members:
            archive.writestr(name, payload)
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("synthetic-bank.zip", inner.getvalue())
    return outer.getvalue()


def parsed(rows, *, profile=None):
    raw = csv_bytes(rows)
    kwargs = {} if profile is None else {"profile": profile}
    return table_archive.parse(io.BytesIO(envelope([("bank/flows.csv", raw)])), **kwargs), raw


def test_archive_v4_exact_currency_code_header_keeps_currency_and_member_trace():
    plan, raw = parsed([
        ["date", "payer_account", "payee_account", "amount", "币种代码"],
        ["2026-01-01", "SYN-A", "SYN-B", "123.45", "CNY"],
        ["2026-01-02", "SYN-B", "SYN-A", "2.03", "CNY"],
        ["2026-01-03", "SYN-A", "SYN-C", "4.56", "USD"],
    ])
    assert plan["stats"]["currency_totals"] == {"CNY": "125.48", "USD": "4.56"}
    assert plan["profile"] == V4
    assert [(row["amount"], row["currency"]) for row in plan["rows"]] == [
        (Decimal("123.45"), "CNY"), (Decimal("2.03"), "CNY"), (Decimal("4.56"), "USD")]
    member = plan["members"][0]
    path = "synthetic-bank.zip/bank/flows.csv"
    digest = hashlib.sha256(raw).hexdigest()
    assert member["member_path"] == path and member["sha256"] == digest
    assert [record["source_row"] for record in plan["records"]] == [2, 3, 4]
    assert [record["record_key"] for record in plan["records"]] == [
        table_import.record_key(path, digest, position) for position in (2, 3, 4)]


def test_archive_v4_retains_overlapping_layouts_until_an_unmistakable_header():
    plan, _ = parsed([
        ["date", "payer_account", "payee_account", "amount", "extra1", "extra2"],
        ["2026-01-01", "SYN-A", "SYN-B", "1.23", "", ""],
        ["2026-01-02", "payee_account", "payer_account", "100", "date", "amount"],
        ["2026-01-04", "SYN-D", "SYN-C", "99.00", "2026-01-03", "2.34"],
        ["date", "payee_account", "payer_account", "amount", "extra1", "extra2"],
        ["2026-01-05", "SYN-E", "SYN-F", "3.45", "", ""],
    ])
    assert plan["stats"]["currency_totals"] == {"CNY": "4.68"}
    assert (plan["stats"]["accepted"], plan["stats"]["unresolved"]) == (2, 2)
    assert [(row["payer_account"], row["payee_account"], row["amount"]) for row in plan["rows"]] == [
        ("SYN-A", "SYN-B", Decimal("1.23")), ("SYN-F", "SYN-E", Decimal("3.45"))]
    assert [(record["source_row"], record["reason_code"]) for record in plan["records"]] == [
        (2, "transaction_valid"), (3, "ambiguous_region_header"),
        (4, "ambiguous_region_header"), (5, "repeated_header"), (6, "transaction_valid")]
    assert plan["documents"][0]["header_rows"] == [1, 5]


def test_archive_member_optins_do_not_enter_standalone_or_advanced_inheritance():
    assert table_archive.PROFILE == table_import.ARCHIVE_MEMBER_PROFILE == V4
    assert table_archive.CANCELLATION_PROFILE == V3
    assert table_archive.CANCELLATION_PROFILES == {
        V3, V4, table_archive.LEGACY_EVIDENCE_PROFILE, table_archive.EVIDENCE_PROFILE, table_archive.XLS_EVIDENCE_PROFILE, *table_import.MICROSECOND_ARCHIVE_PROFILES}
    assert V4 not in table_import.SUPPORTED_PROFILES
    assert table_import.CURRENCY_CODE_ALIAS_PROFILES == {
        table_import.CURRENCY_REGION_PROFILE, table_import.XLS_PROFILE, V4,
        table_import.LEGACY_EVIDENCE_PROFILE, table_archive.LEGACY_EVIDENCE_PROFILE,
        table_import.EVIDENCE_PROFILE, table_archive.EVIDENCE_PROFILE, table_archive.XLS_EVIDENCE_PROFILE, *table_import.MICROSECOND_ARCHIVE_PROFILES}
    assert table_import.OVERLAPPING_HEADER_PROFILES == {
        table_import.CURRENCY_REGION_PROFILE, table_import.XLS_PROFILE, V4,
        table_import.LEGACY_EVIDENCE_PROFILE, table_archive.LEGACY_EVIDENCE_PROFILE,
        table_import.EVIDENCE_PROFILE, table_archive.EVIDENCE_PROFILE, table_archive.XLS_EVIDENCE_PROFILE, *table_import.MICROSECOND_ARCHIVE_PROFILES}
    for collection in (
            table_import.ROLE_PROFILES, table_import.FINANCIAL_PROFILES,
            table_import.CONTRACT_PROFILES, table_import.DIRECTION_PROFILES,
            table_import.TIME_FORMAT_PROFILES, table_import.UNIT_DECLARATION_PROFILES,
            table_import.SOURCE_ROLE_PROFILES, table_import.BANK_NAME_PROFILES,
            table_import.QUERY_OWNER_PROFILES, table_import.REGION_HEADER_PROFILES,
            table_import.REORDERED_TIME_PROFILES, table_import.CURRENCY_REGION_PROFILES):
        assert V4 not in collection
    assert table_import.PROFILE == "funds-trace-table-v17"
    assert table_import.XLS_PROFILE == "funds-trace-table-v18"


@pytest.mark.parametrize("suffix,reference_policy", [
    (".csv", None), (".csv", "unknown"),
    (".txt", table_import.TRANSACTION_REFERENCE_POLICY),
    (".tsv", table_import.TRANSACTION_REFERENCE_POLICY),
    (".xlsx", table_import.TRANSACTION_REFERENCE_POLICY),
    (".xls", table_import.TRANSACTION_REFERENCE_POLICY),
])
def test_archive_member_rule_requires_csv_and_existing_reference_policy(suffix, reference_policy):
    raw = csv_bytes([["date", "payer_account", "payee_account", "amount"],
                     ["2026-01-01", "SYN-A", "SYN-B", "1.23"]])
    with pytest.raises(table_import.TableImportError):
        table_import.parse(raw, suffix=suffix, profile=V4, reference_policy=reference_policy)


@pytest.mark.parametrize("code,other,expected,reason", [
    ("USD", "USD", "USD", "transaction_valid"),
    ("CNY", "RMB", "CNY", "transaction_valid"),
    ("RMB", "人民币", "CNY", "transaction_valid"),
    ("美元", "USD", "USD", "transaction_valid"),
    ("", "USD", "USD", "transaction_valid"),
    ("USD", "", "USD", "transaction_valid"),
    ("", "", "CNY", "transaction_valid"),
    ("USD", "CNY", None, "invalid_currency"),
    ("UNKNOWN", "USD", None, "invalid_currency"),
    ("02", "", None, "invalid_currency"),
])
def test_archive_currency_code_validates_all_nonempty_candidates(code, other, expected, reason):
    plan, _ = parsed([
        ["date", "payer_account", "payee_account", "amount", "币种代码", "币种"],
        ["2026-01-01", "000SYN-A", "000SYN-B", "1.23", code, other],
    ])
    assert plan["records"][0]["reason_code"] == reason
    assert [(row["currency"], row["payer_account"], row["payee_account"], row["amount"])
            for row in plan["rows"]] == (
        [(expected, "000SYN-A", "000SYN-B", Decimal("1.23"))] if expected else [])
    assert {(item["column_index"], item["candidate_count"]) for item in plan["mapping_evidence"]
            if item["field"] == "currency"} == {(5, 2), (6, 2)}


@pytest.mark.parametrize("profile", [
    "funds-trace-table-archive-v1", "funds-trace-table-archive-v2", V3,
])
def test_explicit_historical_archive_currency_interpretations_remain_frozen(profile):
    plan, _ = parsed([
        ["date", "payer_account", "payee_account", "amount", "币种代码"],
        ["2026-01-01", "SYN-A", "SYN-B", "123.45", "CNY"],
        ["2026-01-02", "SYN-B", "SYN-A", "2.03", "CNY"],
        ["2026-01-03", "SYN-A", "SYN-C", "4.56", "USD"],
    ], profile=profile)
    assert plan["profile"] == profile
    assert plan["stats"]["currency_totals"] == {"CNY": "130.04"}
    assert [record["reason_code"] for record in plan["records"]] == ["transaction_valid"] * 3
    assert not any(item["field"] == "currency" for item in plan["mapping_evidence"])


@pytest.mark.parametrize("rows", [
    [["date", "payer_account", "payee_account", "amount", "currency"],
     ["2026-01-01", "SYN-A", "SYN-B", "1.23", "USD"]],
    [["date", "account", "counterparty account", "amount", "credit/debit"],
     ["2026-01-01", "SYN-A", "SYN-B", "-1.23", "out"]],
    [["date", "payer_account", "payee_account", "amount", "交易对方账号开户行"],
     ["2026-01-01", "SYN-A", "SYN-B", "1.23", "Synthetic bank"]],
    [["date", "查询帐号", "counterparty account", "amount", "credit/debit"],
     ["2026-01-01", "SYN-A", "SYN-B", "1.23", "out"]],
    [["date", "本方卡号", "交易对方卡号", "amount", "credit/debit"],
     ["2026-01-01", "SYN-A", "SYN-B", "1.23", "out"]],
    [["date", "payer_account", "payee_account", "amount", "amount unit", "交易时间(Unix秒)"],
     ["2026-01-01", "SYN-A", "SYN-B", "123", "分", "1767225600"]],
])
def test_archive_v4_other_rules_keep_the_complete_v3_plan(rows):
    previous, _ = parsed(rows, profile=V3)
    current, _ = parsed(rows)
    previous["profile"] = V4
    assert current == previous


@pytest.mark.parametrize("payer,payee", [
    ("date", "amount"), ("payer_account", "SYN-B"), ("SYN-A", "payee_account"),
])
def test_archive_v4_keeps_legal_account_and_extra_header_words(payer, payee):
    plan, _ = parsed([
        ["date", "payer_account", "payee_account", "amount", "extra1", "extra2"],
        ["2026-01-01", payer, payee, "1.23", "date", "amount"],
    ])
    assert plan["records"][0]["reason_code"] == "transaction_valid"
    assert [(row["payer_account"], row["payee_account"], row["amount"])
            for row in plan["rows"]] == [(payer, payee, Decimal("1.23"))]


def test_archive_v4_ambiguity_stays_inside_its_member_and_independent_header_is_valid():
    from test_table_currency_region_v17 import HEADERS, FIRST, OVERLAP, SECOND

    first = csv_bytes([HEADERS, FIRST, OVERLAP, SECOND])
    second = csv_bytes([OVERLAP, SECOND])
    plan = table_archive.parse(io.BytesIO(envelope([
        ("bank/ambiguous.csv", first), ("bank/independent.csv", second)])))
    assert plan["stats"]["currency_totals"] == {"CNY": "3.57"}
    assert (plan["stats"]["accepted"], plan["stats"]["unresolved"]) == (2, 2)
    assert [(row["payer_account"], row["payee_account"], row["amount"])
            for row in plan["rows"]] == [
        ("SYN-A", "SYN-B", Decimal("1.23")), ("SYN-C", "SYN-D", Decimal("2.34"))]
    assert plan["records"][-1]["member_path"].endswith("bank/independent.csv")
    assert plan["records"][-1]["source_row"] == 2


def test_archive_v3_cancellation_and_v4_use_the_identical_closed_policy():
    from test_table_cancellations import round_trip, source

    raw = source(round_trip())[1]
    previous = table_archive.parse(io.BytesIO(raw), profile=V3)
    current = table_archive.parse(io.BytesIO(raw))
    assert previous["stats"]["cancelled"] == current["stats"]["cancelled"] == 2
    assert current["cancellations"] == [{
        "original_record_no": 2, "cancellation_record_no": 3, "witness_record_no": 1}]
    previous["profile"] = V4
    assert current == previous
