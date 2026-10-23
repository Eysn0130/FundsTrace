"""Independent synthetic examples for the closed bank-feedback ZIP profile."""
import csv
from decimal import Decimal, localcontext
import hashlib
import io
import json
import stat
import struct
import warnings
import zipfile

import pytest

from app import feedback_import as feedback


def transaction(**changes):
    row = dict.fromkeys(feedback.COLUMNS, "")
    row.update({"交易卡号": "000-CARD", "交易账号": "000-ACCOUNT",
                "交易时间": "2026-01-02 03:04:05", "交易金额": "12.34",
                "交易余额": "100.01", "收付标志": "出", "交易对手账卡号": "000-OTHER",
                "对手户名": "Synthetic counterparty", "对手开户银行": "Synthetic bank",
                "摘要说明": "Synthetic summary", "交易币种": "CNY", "交易是否成功": "01",
                "交易流水号": "SYN-001", "交易类型": "Synthetic transfer"})
    row.update(changes)
    return row


def table(role, rows, *, encoding="utf-8-sig", headers=None, padded=False):
    headers = list(headers or feedback.SCHEMAS[role])
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n", quoting=csv.QUOTE_ALL)
    writer.writerow([cell + "\t" if padded else cell for cell in headers])
    for row in rows:
        cells = [row.get(key, "") for key in headers] if isinstance(row, dict) else row
        writer.writerow([str(cell) + "\t" if padded else cell for cell in cells])
    return stream.getvalue().encode(encoding)


def archive(members, *, compression=zipfile.ZIP_DEFLATED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as writer:
        for name, content in members:
            writer.writestr(name, content)
    stream.seek(0)
    return stream


def parse_rows(rows, *, profile=feedback.PROFILE, **options):
    return feedback.parse(archive([("synthetic/transactions.csv", table("transaction", rows, **options))]), profile=profile)


def reject(source, code):
    with pytest.raises(feedback.FeedbackImportError) as caught:
        feedback.parse(source)
    assert caught.value.code == code
    assert "000-ACCOUNT" not in str(caught.value)
    return caught.value


def test_closed_mapping_uses_only_mode_b_fields():
    from app import importer
    assert set(feedback.MAPPING) <= set(importer.MODE_FIELDS["B"])
    assert set(feedback.MAPPING.values()) <= set(feedback.COLUMNS)
    assert importer._validate(feedback.MAPPING, "B")


def test_complete_record_accounting_logical_rows_and_deterministic_source_references():
    headers = list(reversed(feedback.COLUMNS))
    tx = table("transaction", [transaction(**{"摘要说明": "two\nphysical lines", "备注": "extra"}),
                               transaction(**{"交易金额": "-2.50", "摘要说明": "冲正 signal"})],
               headers=headers, padded=True)
    auxiliary = table("phone", [{"开户名称": "Synthetic", "联系电话": "SYN-PHONE"}])
    source = archive([("data/tx.csv", tx), ("other/contact.csv", auxiliary)])
    result = feedback.parse(source)
    assert feedback.parse(source) == result
    assert result["profile"] == feedback.PROFILE
    assert [(r["record_no"], r["source_row"], r["disposition"]) for r in result["records"]] == [
        (1, 2, "accepted"), (2, 3, "unresolved"), (3, 2, "auxiliary")]
    digest = hashlib.sha256(tx).hexdigest()
    identity_digest = hashlib.sha256(json.dumps(
        ["", ""],
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    independent_key = hashlib.sha256(json.dumps(
        ["funds-trace-bank-feedback-v1", "data/tx.csv", digest, 2, identity_digest],
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    row = result["rows"][0]
    assert row["_record_key"] == result["records"][0]["record_key"] == independent_key
    assert result["records"][0]["details"]["own_identity_sha256"] == identity_digest
    assert row["_source_row"] == 1 and row["_source_sheet"] == ""
    assert row["_source_row_kind"] == "archive_record"
    assert row["summary"] == "two\nphysical lines\n备注：extra"
    assert result["members"][0] == {"member_path": "data/tx.csv", "role": "transaction",
                                     "sha256": digest, "size_bytes": len(tx),
                                     "encoding": "utf-8-sig", "row_count": 2}
    stats = result["stats"]
    assert {key: stats[key] for key in ("total", "ok", "skipped", "review", "non_transaction",
                                      "auxiliary", "record_total", "members")} == {
        "total": 2, "ok": 1, "skipped": 1, "review": 0, "non_transaction": 0,
        "auxiliary": 1, "record_total": 3, "members": 2}
    assert stats["reason_counts"] == {"transaction_valid": 1, "negative_reversal_text": 1,
                                      "auxiliary_table": 1}
    assert stats["currency_totals"] == {"CNY": "12.34"}
    assert result["records"][1]["details"]["signed_amount"] == "-2.50"
    assert result["records"][1]["details"]["event_type"] == "reversal_text"
    assert all(set(record["details"]) <= feedback.DETAILS_FIELDS for record in result["records"])
    assert all(isinstance(value, str) for record in result["records"] for value in record["details"].values())


@pytest.mark.parametrize("direction,own_side", [("出", "payer"), ("进", "payee"), ("入", "payee")])
def test_missing_counterparty_is_unknown_and_names_never_become_accounts(direction, own_side):
    result = parse_rows([transaction(**{"收付标志": direction, "交易对手账卡号": "",
                                        "对手户名": "Synthetic name only"})])
    row = result["rows"][0]
    other_side = "payee" if own_side == "payer" else "payer"
    assert row[own_side + "_account"] == "000-ACCOUNT"
    assert row[other_side + "_account"] == ""
    assert row[other_side + "_name"] == "Synthetic name only"
    assert result["records"][0]["details"]["account_card_relation"] == "both_present_distinct"
    assert result["records"][0]["details"]["source_columns"] == "交易账号,交易卡号"


def test_account_fallback_is_per_record_and_keeps_identity_spelling():
    result = parse_rows([transaction(**{"交易账号": "", "交易卡号": " 000_/A-01 ",
                                        "交易对手账卡号": " 000_/B-02 "})])
    row = result["rows"][0]
    assert row["self_account"] == row["payer_account"] == "000_/A-01"
    assert row["payee_account"] == "000_/B-02"
    assert result["records"][0]["details"]["source_account_column"] == "交易卡号"


def test_unique_same_column_account_evidence_can_follow_the_transactions():
    account = {"交易账号": "000-ACCOUNT", "交易卡号": "000-CARD",
               "账户开户名称": "Synthetic owner", "账号开户银行": "Synthetic own bank"}
    result = feedback.parse(archive([
        ("not-an-identity/tx.csv", table("transaction", [transaction()])),
        ("not-an-identity/account.csv", table("account", [account, account])),
    ]))
    row = result["rows"][0]
    assert row["self_name"] == row["payer_name"] == "Synthetic owner"
    assert row["payer_bank"] == "Synthetic own bank"
    assert result["records"][0]["details"]["own_identity_record"] == "2"
    assert result["stats"]["auxiliary"] == 2


def test_conflicting_or_cross_column_account_evidence_does_not_enrich():
    accounts = [{"交易账号": "000-ACCOUNT", "账户开户名称": "Synthetic A", "账号开户银行": "Bank A"},
                {"交易账号": "000-ACCOUNT", "账户开户名称": "Synthetic B", "账号开户银行": "Bank B"},
                {"交易卡号": "OTHER-ACCOUNT", "账户开户名称": "Synthetic C", "账号开户银行": "Bank C"}]
    result = feedback.parse(archive([
        ("accounts.csv", table("account", accounts)),
        ("Synthetic filename is not owner.csv", table("transaction", [transaction(), transaction(**{"交易账号": "OTHER-ACCOUNT"})])),
    ]))
    assert all(row["self_name"] == row["payer_name"] == row["payer_bank"] == "" for row in result["rows"])
    assert all("own_identity_record" not in record["details"] for record in result["records"])


def test_record_identity_binds_resolved_name_and_bank_but_not_auxiliary_location():
    tx = table("transaction", [transaction()])
    account_a = table("account", [{"交易账号": "000-ACCOUNT", "账户开户名称": "Synthetic owner A",
                                   "账号开户银行": "Synthetic bank A"}])
    account_b = table("account", [{"交易账号": "000-ACCOUNT", "账户开户名称": "Synthetic owner B",
                                   "账号开户银行": "Synthetic bank B"}])
    first = feedback.parse(archive([("tx.csv", tx), ("account.csv", account_a)]))
    changed = feedback.parse(archive([("tx.csv", tx), ("account.csv", account_b)]))
    relocated = feedback.parse(archive([("tx.csv", tx), ("different/account.csv", account_a)]))
    assert first["members"][0]["sha256"] == changed["members"][0]["sha256"]
    assert first["rows"][0]["self_name"] == "Synthetic owner A"
    assert changed["rows"][0]["self_name"] == "Synthetic owner B"
    assert first["rows"][0]["payer_bank"] == "Synthetic bank A"
    assert changed["rows"][0]["payer_bank"] == "Synthetic bank B"
    assert first["records"][0]["record_key"] != changed["records"][0]["record_key"]
    assert first["records"][0]["record_key"] == relocated["records"][0]["record_key"]
    assert first["records"][1]["record_key"] != changed["records"][1]["record_key"]
    assert all(result["rows"][0]["_record_key"] == result["records"][0]["record_key"]
               for result in (first, changed, relocated))


def test_repacking_identical_data_context_preserves_keys_across_order_compression_and_folders():
    tx = ("synthetic/tx.csv", table("transaction", [transaction(), transaction(**{"交易金额": "-1.00"})]))
    account = ("synthetic/account.csv", table("account", [{"交易账号": "000-ACCOUNT",
                                                          "账户开户名称": "Synthetic owner"}]))
    first = feedback.parse(archive([tx, account], compression=zipfile.ZIP_STORED))
    repacked = feedback.parse(archive([("synthetic/", b""), account, tx]))
    by_location = lambda result: {(record["member_path"], record["source_row"]): record["record_key"]
                                  for record in result["records"]}
    assert by_location(first) == by_location(repacked)
    assert first["rows"][0]["_record_key"] == repacked["rows"][0]["_record_key"]
    assert first["records"][0]["record_no"] != repacked["records"][1]["record_no"]


@pytest.mark.parametrize("field,value", [("账户开户名称", "Different owner"), ("账号开户银行", "Different bank")])
def test_changing_each_used_identity_fact_changes_accepted_record_keys(field, value):
    tx = ("tx.csv", table("transaction", [transaction(**{"收付标志": "进"})]))
    account = {"交易账号": "000-ACCOUNT", "账户开户名称": "Synthetic owner", "账号开户银行": "Synthetic bank"}
    first = feedback.parse(archive([tx, ("account.csv", table("account", [account]))]))
    changed = feedback.parse(archive([tx, ("account.csv", table("account", [{**account, field: value}]))]))
    assert first["records"][0]["record_key"] != changed["records"][0]["record_key"]
    assert first["records"][0]["details"]["own_identity_sha256"] != changed["records"][0]["details"]["own_identity_sha256"]
    assert changed["records"][0]["details"]["own_identity_sha256"] == feedback.identity_digest(
        changed["rows"][0]["self_name"], changed["rows"][0]["payee_bank"])


def test_unrelated_auxiliary_changes_cannot_change_accepted_record_keys():
    tx = ("tx.csv", table("transaction", [transaction()]))
    account = {"交易账号": "000-ACCOUNT", "账户开户名称": "Synthetic owner", "账号开户银行": "Synthetic bank"}
    first = feedback.parse(archive([tx, ("account.csv", table("account", [account])),
                                    ("phone.csv", table("phone", [{"联系电话": "Synthetic A"}]))]))
    changed = feedback.parse(archive([("phone.csv", table("phone", [{"联系电话": "Synthetic B"}])), tx,
                                      ("account.csv", table("account", [{**account, "备注": "Different unused remark"}]))]))
    assert first["rows"][0]["_record_key"] == changed["rows"][0]["_record_key"]
    assert first["records"][0]["details"]["own_identity_record"] != changed["records"][1]["details"]["own_identity_record"]


def test_unresolved_to_resolved_identity_changes_keys_only_when_facts_change():
    tx = ("tx.csv", table("transaction", [transaction()]))
    unresolved = feedback.parse(archive([tx]))
    empty_identity = feedback.parse(archive([tx, ("account.csv", table("account", [{"交易账号": "000-ACCOUNT"}]))]))
    resolved = feedback.parse(archive([tx, ("account.csv", table("account", [{"交易账号": "000-ACCOUNT",
                                                                             "账户开户名称": "Synthetic owner"}]))]))
    assert unresolved["rows"][0]["_record_key"] == empty_identity["rows"][0]["_record_key"]
    assert unresolved["rows"][0]["_record_key"] != resolved["rows"][0]["_record_key"]
    assert "own_identity_record" in empty_identity["records"][0]["details"]


@pytest.mark.parametrize("changes,reason", [
    ({"交易金额": "-1.00"}, "negative_unknown"),
    ({"交易金额": "-1.00", "摘要说明": "冲正"}, "negative_reversal_text"),
    ({"交易金额": "-1.00", "备注": "撤销"}, "negative_revocation_text"),
    ({"交易金额": "-1.00", "交易类型": "冲正撤销"}, "negative_reversal_revocation_text"),
    ({"摘要说明": "冲正"}, "reversal_text_requires_review"),
    ({"备注": "撤销"}, "reversal_text_requires_review"),
    ({"交易金额": "0.00"}, "zero_amount"),
    ({"交易金额": "1.001"}, "amount_precision"),
    ({"交易金额": "10000000000000000.00"}, "amount_overflow"),
    ({"交易金额": "USD12.34"}, "invalid_amount"),
    ({"交易金额": "1,000.00"}, "invalid_amount"),
    ({"交易金额": "1e3"}, "invalid_amount"),
    ({"交易金额": "NaN"}, "invalid_amount"),
    ({"交易金额": "Infinity"}, "invalid_amount"),
    ({"交易金额": ""}, "missing_core"),
    ({"交易账号": "", "交易卡号": ""}, "missing_core"),
    ({"收付标志": "转出"}, "invalid_direction"),
    ({"收付标志": "OUT"}, "invalid_direction"),
    ({"交易时间": "0002-01-02 03:04:05"}, "invalid_time"),
    ({"交易时间": "2101-01-02 03:04:05"}, "invalid_time"),
    ({"交易时间": "2026-02-30 03:04:05"}, "invalid_time"),
    ({"交易时间": "2026-1-02 03:04:05"}, "invalid_time"),
    ({"交易时间": "2026/01/02 03:04:05"}, "invalid_time"),
    ({"交易时间": "2026-01-02T03:04:05"}, "invalid_time"),
    ({"交易币种": ""}, "invalid_currency"),
    ({"交易币种": "cny"}, "invalid_currency"),
    ({"交易币种": "UNKNOWN"}, "invalid_currency"),
    ({"交易是否成功": "02"}, "unknown_status"),
    ({"交易是否成功": ""}, "unknown_status"),
    ({"交易余额": "1.001"}, "balance_precision"),
    ({"交易余额": "10000000000000000.00"}, "balance_overflow"),
    ({"交易余额": "not money"}, "invalid_balance"),
])
@pytest.mark.parametrize("profile", [feedback.IDENTITY_PROFILE, feedback.PROFILE])
def test_ambiguous_or_unrepresentable_values_have_versioned_dispositions(changes, reason, profile):
    result = parse_rows([transaction(**changes)], profile=profile)
    if profile == feedback.PROFILE and reason == "reversal_text_requires_review":
        assert result["stats"]["ok"] == 1 and result["stats"]["skipped"] == 0
        assert result["rows"][0]["amount"] == Decimal("12.34")
        assert result["rows"][0]["payer_account"] == "000-ACCOUNT"
        assert result["records"][0]["details"]["financial_effect"] == "source_reported"
        return
    disposition = ("review" if profile == feedback.IDENTITY_PROFILE else
                   "non_monetary" if reason == "zero_amount" else "unresolved")
    assert result["rows"] == []
    assert result["stats"]["total"] == result["stats"][disposition] == result["stats"]["skipped"] == 1
    assert result["stats"]["currency_totals"] == {}
    record = result["records"][0]
    assert record["disposition"] == disposition and record["reason_code"] == reason
    field = "review_reasons" if profile == feedback.IDENTITY_PROFILE else "validation_issues"
    assert reason in record["details"][field].split(",")


def test_negative_records_keep_all_review_signals_and_reported_status():
    result = parse_rows([transaction(**{"交易金额": "-3.00", "摘要说明": "冲正",
                                        "交易是否成功": "02", "收付标志": "?", "交易时间": "bad"})])
    record = result["records"][0]
    assert record["reason_code"] == "negative_reversal_text"
    assert record["details"]["signed_amount"] == "-3.00"
    assert record["details"]["reported_status"] == "02"
    assert record["details"]["source_direction"] == "?"
    assert set(record["details"]["validation_issues"].split(",")) == {
        "negative_reversal_text", "invalid_direction", "invalid_time", "unknown_status"}


@pytest.mark.parametrize("amount", ["0", "-0.00", "+0.00000", "0" * 200 + ".00"])
def test_exact_zero_never_invents_direction_or_requires_missing_transaction_fields(amount):
    result = parse_rows([transaction(**{"交易金额": amount, "交易时间": "bad", "收付标志": "",
                                        "交易账号": "", "交易卡号": "", "交易币种": "",
                                        "交易是否成功": "02", "交易余额": "bad"})])
    assert result["rows"] == [] and result["stats"]["non_monetary"] == 1
    record = result["records"][0]
    assert record["reason_code"] == "zero_amount"
    assert Decimal(record["details"]["canonical_amount"]) == 0
    assert record["details"]["financial_effect"] == "no_movement"
    assert set(record["details"]["validation_issues"].split(",")) == {
        "missing_core", "zero_amount", "invalid_direction", "invalid_time", "invalid_currency",
        "unknown_status", "invalid_balance"}


@pytest.mark.parametrize("amount", ["0.001", "-0.001", "1e-200", "0.00USD", ""])
def test_nonzero_or_invalid_amounts_never_round_into_non_monetary(amount):
    result = parse_rows([transaction(**{"交易金额": amount})])
    assert result["rows"] == [] and result["stats"]["non_monetary"] == 0
    assert result["stats"]["unresolved"] == 1


@pytest.mark.parametrize("direction,side", [("出", "payer"), ("进", "payee"), ("入", "payee")])
@pytest.mark.parametrize("text", ["冲正", "撤销", "未冲正", "不撤销", "冲正撤销"])
def test_positive_reversal_words_are_annotations_and_never_net_or_flip(direction, side, text):
    normal = transaction(**{"收付标志": direction})
    reported = transaction(**{"收付标志": direction, "摘要说明": text})
    result = parse_rows([normal, reported])
    assert len(result["rows"]) == 2
    assert result["stats"]["currency_totals"] == {"CNY": "24.68"}
    assert all(row[side + "_account"] == "000-ACCOUNT" for row in result["rows"])
    assert all(row["amount"] == Decimal("12.34") for row in result["rows"])
    assert result["records"][1]["details"]["financial_effect"] == "source_reported"


def test_no_direction_is_not_recovered_from_refund_text_balance_sign_or_references():
    rows = [transaction(**{"交易流水号": "SAME", "交易金额": "1.00", "交易余额": "10.00"}),
            transaction(**{"交易流水号": "SAME", "交易金额": "-1.00", "摘要说明": "消费退货冲正", "交易余额": "11.00"}),
            transaction(**{"交易金额": "2.00", "收付标志": "", "摘要说明": "收入利息", "交易余额": "13.00"})]
    result = parse_rows(rows)
    assert len(result["rows"]) == 1 and result["stats"]["unresolved"] == 2
    assert result["stats"]["currency_totals"] == {"CNY": "1.00"}
    assert result["records"][1]["details"]["canonical_amount"] == "-1.00"


def test_feedback_with_account_only_is_source_feedback_and_versions_preserve_keys():
    rows = [transaction(), transaction(**{"摘要说明": "冲正"}),
            transaction(**{"交易金额": "0.00"}),
            {"交易账号": "SYN-ACCOUNT", "查询反馈结果原因": "Synthetic no detail"}]
    old = parse_rows(rows, profile=feedback.IDENTITY_PROFILE)
    new = parse_rows(rows)
    assert [r["disposition"] for r in old["records"]] == ["accepted", "review", "review", "review"]
    assert [r["disposition"] for r in new["records"]] == ["accepted", "accepted", "non_monetary", "non_transaction"]
    assert old["records"][0]["record_key"] == new["records"][0]["record_key"]
    assert old["records"][2]["record_key"] == new["records"][2]["record_key"]
    assert set(old["records"][0]["details"]).isdisjoint({"financial_effect", "canonical_amount", "validation_issues"})
    assert sum(new["stats"][k] for k in ("ok", "unresolved", "non_monetary", "non_transaction", "auxiliary")) == 4
    with pytest.raises(feedback.FeedbackImportError, match="unknown_profile"):
        feedback.parse(io.BytesIO(), profile="future-unsupported")


def test_exact_decimal_totals_keep_currencies_and_directions_separate():
    result = parse_rows([
        transaction(**{"交易金额": "0.10", "收付标志": "进", "交易余额": ""}),
        transaction(**{"交易金额": "0.20", "收付标志": "入", "交易余额": "-3.01"}),
        transaction(**{"交易金额": "1.0000", "交易是否成功": "1"}),
        transaction(**{"交易金额": "9999999999999999.99", "交易币种": "USD"}),
        transaction(**{"交易金额": "0.01", "交易币种": "USD"}),
        transaction(**{"交易金额": "2.02", "交易币种": "EUR"}),
        transaction(**{"交易金额": "3.00", "交易币种": "JPY"}),
    ])
    assert result["stats"]["ok"] == 7
    assert result["stats"]["currency_totals"] == {
        "CNY": "1.30", "USD": "10000000000000000.00", "EUR": "2.02", "JPY": "3.00"}
    assert result["stats"]["currency_direction_totals"]["CNY"] == {"in": "0.30", "out": "1.00"}
    assert result["rows"][0]["amount"] == Decimal("0.10")
    assert result["rows"][0]["balance"] is None
    assert result["rows"][1]["balance"] == Decimal("-3.01")
    assert result["rows"][3]["amount"] == Decimal("9999999999999999.99")


def test_money_validation_and_totals_ignore_a_callers_low_decimal_precision():
    with localcontext() as context:
        context.prec = 2
        result = parse_rows([transaction(**{"交易金额": "9999999999999999.99"}),
                             transaction(**{"交易金额": "0.01"})])
    assert result["stats"]["ok"] == 2
    assert result["stats"]["currency_totals"] == {"CNY": "10000000000000000.00"}


def test_all_empty_core_is_feedback_only_and_all_auxiliary_roles_are_counted():
    feedback_row = transaction(**{"交易卡号": "", "交易账号": "", "交易时间": "",
                                  "交易金额": "", "收付标志": "", "查询反馈结果原因": "Synthetic feedback"})
    members = [("feedback.csv", table("transaction", [feedback_row]))]
    members += [(role + ".csv", table(role, [{}])) for role in feedback.ROLES if role != "transaction"]
    result = feedback.parse(archive(members))
    assert result["rows"] == []
    assert result["stats"]["total"] == result["stats"]["non_transaction"] == result["stats"]["skipped"] == 1
    assert result["stats"]["review"] == 0
    assert result["stats"]["auxiliary"] == 7 and result["stats"]["record_total"] == 8
    assert result["records"][0]["reason_code"] == "feedback_only"
    assert {member["role"] for member in result["members"]} == feedback.ROLES


@pytest.mark.parametrize("row", [["short"], [""] * 29, []])
def test_wrong_csv_width_rejects_the_whole_preview_including_prior_valid_rows(row):
    source = archive([("tx.csv", table("transaction", [transaction(), row]))])
    reject(source, "csv_width")


@pytest.mark.parametrize("headers,reason", [
    ([*feedback.COLUMNS[:-1], feedback.COLUMNS[0]], "duplicate_header"),
    ([*feedback.COLUMNS, "unexpected"], "unknown_schema"),
    (list(feedback.COLUMNS[:-1]), "unknown_schema"),
    (["交易金额(分)" if name == "交易金额" else name for name in feedback.COLUMNS], "unknown_schema"),
])
def test_unknown_or_ambiguous_schemas_never_guess(headers, reason):
    reject(archive([("tx.csv", table("transaction", [transaction()], headers=headers))]), reason)


@pytest.mark.parametrize("tail", ['"unterminated', 'unquoted"quote', '"closed"junk'])
def test_malformed_csv_quoting_is_rejected(tail):
    header = table("transaction", [], encoding="utf-8")
    reject(archive([("tx.csv", header + tail.encode())]), "invalid_csv")


@pytest.mark.parametrize("encoding", ["utf-8-sig", "gb18030"])
def test_supported_encoding_is_strict_and_padding_only_is_removed(encoding):
    result = parse_rows([transaction(**{"摘要说明": "合成摘要", "备注": "合成备注"})], encoding=encoding, padded=True)
    assert result["members"][0]["encoding"] == encoding
    assert result["rows"][0]["summary"] == "合成摘要\n备注：合成备注"


@pytest.mark.parametrize("payload,reason", [(b"\xff", "invalid_encoding"),
                                           (b"\x00binary", "binary_member"), (b"", "missing_header")])
def test_binary_or_undecodable_csv_is_rejected(payload, reason):
    reject(archive([("tx.csv", payload)]), reason)


@pytest.mark.parametrize("path", ["../tx.csv", "/tx.csv", "a/../tx.csv", "a\\tx.csv",
                                  "C:/tx.csv", "a//tx.csv", "./tx.csv", "../", "/", "a/../", "a//"])
def test_unsafe_archive_paths_are_rejected_without_extraction(path):
    reject(archive([(path, table("transaction", [transaction()]))]), "unsafe_member_path")


def test_duplicate_members_symlinks_and_non_csv_members_are_rejected():
    payload = table("transaction", [transaction()])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        reject(archive([("tx.csv", payload), ("tx.csv", payload)]), "duplicate_member")
    link = zipfile.ZipInfo("link.csv")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    reject(archive([(link, b"synthetic-target")]), "symlink_member")
    reject(archive([("tx.xlsx", payload)]), "non_csv_member")


def test_explicit_standard_zip_folders_are_safe_metadata_and_count_toward_limits(monkeypatch):
    source = archive([("synthetic/", b""), ("synthetic/tx.csv", table("transaction", [transaction()]))])
    result = feedback.parse(source)
    assert result["stats"]["ok"] == result["stats"]["members"] == result["stats"]["record_total"] == 1
    assert [member["member_path"] for member in result["members"]] == ["synthetic/tx.csv"]
    assert result["records"][0]["record_no"] == 1
    monkeypatch.setattr(feedback, "MAX_MEMBERS", 1)
    reject(source, "member_count")


def test_directory_entries_cannot_have_payloads_or_bypass_file_type_guards():
    tx = ("tx.csv", table("transaction", [transaction()]))
    reject(archive([("synthetic/", b"unexpected payload"), tx]), "directory_payload")
    link = zipfile.ZipInfo("synthetic/")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    reject(archive([(link, b""), tx]), "symlink_member")
    pipe = zipfile.ZipInfo("pipe.csv")
    pipe.create_system = 3
    pipe.external_attr = (stat.S_IFIFO | 0o600) << 16
    reject(archive([(pipe, b""), tx]), "special_member")
    reject(archive([("synthetic/", b"")]), "non_csv_member")


def test_crc_truncation_and_encrypted_headers_are_rejected():
    payload = table("transaction", [transaction()])
    original = archive([("tx.csv", payload)], compression=zipfile.ZIP_STORED).getvalue()
    corrupted = bytearray(original)
    offset = corrupted.index(b"SYN-001")
    corrupted[offset] = ord("X")
    reject(io.BytesIO(corrupted), "invalid_archive")
    reject(io.BytesIO(original[:-20]), "invalid_archive")
    encrypted = bytearray(original)
    local = encrypted.index(b"PK\x03\x04")
    central = encrypted.index(b"PK\x01\x02")
    struct.pack_into("<H", encrypted, local + 6, 1)
    struct.pack_into("<H", encrypted, central + 8, 1)
    reject(io.BytesIO(encrypted), "encrypted_member")


def test_resource_limits_are_enforced_at_the_boundary(monkeypatch):
    payload = table("transaction", [transaction(), transaction()])
    source = archive([("tx.csv", payload)])
    monkeypatch.setattr(feedback, "TEXT_MAX_RECORDS", 1)
    reject(source, "record_count")
    monkeypatch.setattr(feedback, "TEXT_MAX_RECORDS", 1_000_000)
    monkeypatch.setattr(feedback, "MAX_MEMBER_BYTES", len(payload) - 1)
    reject(source, "member_size")
    monkeypatch.setattr(feedback, "MAX_MEMBER_BYTES", 128 * 1024 * 1024)
    monkeypatch.setattr(feedback, "MAX_EXPANDED_BYTES", len(payload) - 1)
    reject(source, "expanded_size")
    monkeypatch.setattr(feedback, "MAX_EXPANDED_BYTES", 300 * 1024 * 1024)
    monkeypatch.setattr(feedback, "MAX_MEMBERS", 1)
    reject(archive([("tx.csv", payload), ("tx2.csv", payload)]), "member_count")


def test_actual_decompression_reads_are_also_bounded(monkeypatch):
    source = archive([("tx.csv", table("transaction", [transaction()]))])
    original = feedback._validate_members

    def lower_limit_after_metadata_check(archive):
        infos = original(archive)
        monkeypatch.setattr(feedback, "MAX_MEMBER_BYTES", 4)
        return infos

    monkeypatch.setattr(feedback, "_validate_members", lower_limit_after_metadata_check)
    reject(source, "member_size")


def test_multibyte_field_size_limit_and_small_details(monkeypatch):
    monkeypatch.setattr(feedback, "MAX_FIELD_BYTES", 100)
    reject(archive([("tx.csv", table("transaction", [transaction(**{"摘要说明": "合" * 40})]))]), "field_size")
    result = parse_rows([transaction(**{"交易是否成功": "x" * 81})])
    details = result["records"][0]["details"]
    assert "reported_status" not in details
    assert details["reported_status_length"] == "81"
    assert details["reported_status_sha256"] == hashlib.sha256(b"x" * 81).hexdigest()


def test_empty_transaction_table_keeps_member_role_and_zero_counts():
    result = parse_rows([])
    assert result["rows"] == result["records"] == []
    assert result["members"][0]["row_count"] == 0
    assert result["stats"]["total"] == result["stats"]["record_total"] == 0
