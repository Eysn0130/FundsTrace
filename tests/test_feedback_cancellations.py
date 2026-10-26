"""Synthetic public-parser cases for the paired effective-analysis policy."""
import csv
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
import io
import zipfile

import pytest

from app import feedback_import as feedback


def transaction(**changes):
    row = dict.fromkeys(feedback.COLUMNS, "")
    row.update({"交易账号": "SYN-ACCOUNT", "交易卡号": "SYN-CARD",
                "交易时间": "2026-01-01 10:00:00", "交易金额": "1.00",
                "交易余额": "100.00", "收付标志": "出", "交易币种": "CNY",
                "交易是否成功": "01", "交易流水号": "SYN-WITNESS",
                "交易对手账卡号": "SYN-COUNTERPARTY", "摘要说明": "Synthetic transfer"})
    row.update(changes)
    return row


def table(role, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n", quoting=csv.QUOTE_ALL)
    columns = feedback.SCHEMAS[role]
    writer.writerow(columns)
    writer.writerows([row.get(column, "") for column in columns] for row in rows)
    return stream.getvalue().encode("utf-8-sig")


def source(rows, *, extra=()):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("synthetic/transactions.csv", table("transaction", rows))
        for path, role, member_rows in extra:
            archive.writestr(path, table(role, member_rows))
    return stream.getvalue()


def parse(rows, *, profile=feedback.CANCELLATION_PROFILE, extra=()):
    return feedback.parse(io.BytesIO(source(rows, extra=extra)), profile=profile)


def round_trip(direction="出", cancellation_direction=None):
    return [transaction(),
            transaction(**{"交易时间": "2026-01-01 10:01:00", "交易金额": "10.00",
                           "交易余额": "90.00" if direction == "出" else "110.00",
                           "收付标志": direction, "交易流水号": "SYN-PAIR"}),
            transaction(**{"交易时间": "2026-01-01 10:02:00", "交易金额": "-10.00",
                           "收付标志": cancellation_direction or direction,
                           "摘要说明": "冲正", "交易流水号": "SYN-PAIR"})]


def assert_unmatched(plan):
    assert plan["cancellations"] == [] and plan["cancelled_rows"] == []
    assert plan["stats"]["cancelled"] == 0
    assert all(record["disposition"] != "cancelled" for record in plan["records"])


@pytest.mark.parametrize("direction,negative_direction", [("出", "出"), ("进", "进"), ("进", "入"), ("入", "进")])
def test_complete_pair_is_excluded_from_analysis_with_exact_source_references(direction, negative_direction):
    rows = round_trip(direction, negative_direction)
    account = {"交易账号": "SYN-ACCOUNT", "账户开户名称": "Synthetic owner", "账号开户银行": "Synthetic bank"}
    plan = parse(rows, extra=[("synthetic/accounts.csv", "account", [account])])
    original, cancellation = plan["records"][1:3]

    assert plan["profile"] == "funds-trace-bank-feedback-v3"
    assert plan["cancellations"] == [{"original_record_no": 2, "cancellation_record_no": 3, "witness_record_no": 1}]
    assert [row["_source_row"] for row in plan["rows"]] == [1]
    assert [row["_source_row"] for row in plan["cancelled_rows"]] == [2]
    assert plan["cancelled_rows"][0]["amount"] == Decimal("10.00")
    assert plan["cancelled_rows"][0]["_record_key"] == original["record_key"]
    for record, role, paired, signed in [(original, "original", "3", "10.00"),
                                         (cancellation, "cancellation", "2", "-10.00")]:
        assert record["disposition"] == "cancelled" and record["reason_code"] == "matched_cancellation"
        assert record["details"]["financial_effect"] == "analysis_excluded"
        assert record["details"]["cancellation_policy"] == "paired-cancellation-v1"
        assert record["details"]["cancellation_role"] == role
        assert record["details"]["paired_record_no"] == paired
        assert record["details"]["cancellation_witness_record_no"] == "1"
        assert record["details"]["signed_amount"] == record["details"]["canonical_amount"] == signed
        assert record["details"]["reported_status"] == "01"
    assert original["details"]["source_direction"] == direction
    assert cancellation["details"]["source_direction"] == negative_direction
    assert original["details"]["own_identity_record"] == "4"
    assert "own_identity_sha256" in original["details"]
    assert "own_identity_sha256" not in cancellation["details"]
    assert "own_identity_record" not in cancellation["details"]
    assert plan["stats"] == {
        "total": 3, "ok": 1, "skipped": 2, "review": 0, "non_transaction": 0,
        "auxiliary": 1, "non_monetary": 0, "unresolved": 0, "cancelled": 2,
        "record_total": 4, "members": 2, "bad_examples": [],
        "reason_counts": {"transaction_valid": 1, "matched_cancellation": 2, "auxiliary_table": 1},
        "currency_totals": {"CNY": "1.00"},
        "currency_direction_totals": {"CNY": {"in": "0.00", "out": "1.00"}}}
    assert all(set(record["details"]) <= feedback.DETAILS_FIELDS for record in plan["records"])
    assert all(isinstance(value, str) for record in plan["records"] for value in record["details"].values())


def test_historical_profiles_keep_their_previous_dispositions_and_record_keys():
    payload = source(round_trip())
    plans = {profile: feedback.parse(io.BytesIO(payload), profile=profile)
             for profile in (feedback.IDENTITY_PROFILE, feedback.PREVIOUS_PROFILE, feedback.CANCELLATION_PROFILE,
                             feedback.TEXT_PROFILE, feedback.PROFILE)}
    assert feedback.SUPPORTED_PROFILES == frozenset(plans) | {feedback.EVIDENCE_PROFILE}
    assert feedback.PREVIOUS_PROFILE == "funds-trace-bank-feedback-v2"
    for profile in (feedback.IDENTITY_PROFILE, feedback.PREVIOUS_PROFILE):
        old = plans[profile]
        assert "cancelled" not in old["stats"] and "cancellations" not in old and "cancelled_rows" not in old
        assert [record["disposition"] for record in old["records"]] == [
            "accepted", "accepted", "review" if profile == feedback.IDENTITY_PROFILE else "unresolved"]
        assert old["stats"]["currency_totals"] == {"CNY": "11.00"}
    assert plans[feedback.PROFILE]["records"][1]["record_key"] == plans[feedback.PREVIOUS_PROFILE]["records"][1]["record_key"]
    assert plans[feedback.PROFILE]["records"][2]["record_key"] == plans[feedback.PREVIOUS_PROFILE]["records"][2]["record_key"]
    for old in (plans[feedback.IDENTITY_PROFILE], plans[feedback.PREVIOUS_PROFILE]):
        before = old["stats"].copy()
        assert feedback.refresh_stats(old) == before


def test_explicit_v3_keeps_paired_policy_when_the_default_profile_changes(monkeypatch):
    monkeypatch.setattr(feedback, "PROFILE", feedback.IDENTITY_PROFILE)
    plan = feedback.parse(io.BytesIO(source(round_trip())), profile=feedback.CANCELLATION_PROFILE)
    assert plan["stats"]["cancelled"] == 2
    assert plan["cancellations"] == [{"original_record_no": 2, "cancellation_record_no": 3, "witness_record_no": 1}]
    assert feedback.refresh_stats(plan)["cancelled"] == 2


def test_separate_source_columns_do_not_create_a_negated_phrase():
    rows = round_trip()
    # An ordinary type in a separate field neither negates the action nor
    # supplies the affirmative action evidence itself.
    rows[2].update({"摘要说明": "无", "备注": "冲正", "交易类型": "Synthetic type"})
    assert parse(rows)["stats"]["cancelled"] == 2


@pytest.mark.parametrize("changes", [
    {"摘要说明": "冲正", "备注": "POS消费", "交易类型": "银行卡交易"},
    {"摘要说明": "线下商户消费", "备注": "冲正", "交易类型": "消费"},
    {"摘要说明": "冲正", "备注": "失败记录已归档", "交易类型": "交易通道"},
    {"摘要说明": "已冲正", "备注": "未完成事项说明", "交易类型": "消费"},
])
def test_ordinary_business_descriptions_in_fields_without_actions_are_compatible(changes):
    rows = round_trip()
    rows[2].update(changes)
    assert parse(rows)["stats"]["cancelled"] == 2


@pytest.mark.parametrize("changes", [
    {"摘要说明": "无异常", "备注": "冲正", "交易类型": "交易正常"},
    {"摘要说明": "冲正", "备注": "未发现异常", "交易类型": ""},
    {"摘要说明": "", "备注": "无", "交易类型": "撤销成功"},
])
def test_explicit_neutral_annotations_can_accompany_actions_across_source_fields(changes):
    rows = round_trip()
    rows[2].update(changes)
    assert parse(rows)["stats"]["cancelled"] == 2


@pytest.mark.parametrize("text", ["冲正", "已冲正", "冲正成功", "撤销", "撤销成功", "冲正撤销", "无异常，冲正", "交易正常；未发现异常；撤销",
                                  "交易冲正", "交易撤销", "冲正交易", "撤销交易", "消费冲正", "转账冲正",
                                  "已撤销", "成功冲正", "完成撤销", "已完成冲正", "已经成功撤销",
                                  "交易冲正完成", "冲正（成功）", "撤销(完成)", "无异常\n冲正"])
def test_explicit_cancellation_annotation_can_match_without_global_negation(text):
    rows = round_trip()
    rows[2]["摘要说明"] = text
    assert parse(rows)["stats"]["cancelled"] == 2


@pytest.mark.parametrize("text", ["未冲正", "未能冲正", "尚未完成冲正", "未成功冲正", "未能进行冲正", "不冲正", "不再冲正", "无冲正", "无需撤销", "不需要撤销", "不予撤销", "非冲正", "冲正失败", "冲正交易失败", "冲正（失败）", "撤销(申请)", "撤销失败", "冲正未成功", "撤销不成功", "冲正申请", "申请冲正", "冲正处理中", "正在撤销", "撤销待处理", "冲正待确认", "取消冲正", "冲正取消", "退款", "退货", "Synthetic transfer"])
def test_failed_pending_negated_or_refund_text_never_cancels(text):
    rows = round_trip()
    rows[2]["摘要说明"] = text
    plan = parse(rows)
    assert_unmatched(plan)
    assert plan["records"][2]["disposition"] == "unresolved"
    assert len(plan["rows"]) == 2


@pytest.mark.parametrize("text", ["无法冲正", "无法撤销", "未被冲正", "尚未完成的冲正", "冲正无法办理",
                                  "是否冲正", "冲正查询", "尝试执行冲正", "冲正成功但需要核实", "冲正相关业务",
                                  "冲正？", "冲正已", "撤销已经", "冲正，无法撤销", "是否冲正；冲正"])
def test_free_text_containing_cancellation_words_does_not_imply_an_action(text):
    rows = round_trip()
    rows[2]["摘要说明"] = text
    plan = parse(rows)
    assert_unmatched(plan)
    assert plan["records"][2]["disposition"] == "unresolved"


@pytest.mark.parametrize("text", ["冲正，失败", "撤销；未完成", "冲正\n失败", "冲正，无法办理",
                                  "冲正，未知处理结果", "撤销；业务信息未识别"])
def test_every_action_field_fragment_must_have_recognized_semantics(text):
    rows = round_trip()
    rows[2]["摘要说明"] = text
    plan = parse(rows)
    assert_unmatched(plan)
    assert [record["disposition"] for record in plan["records"]] == ["accepted", "accepted", "unresolved"]


@pytest.mark.parametrize("changes", [
    {"摘要说明": "冲正", "备注": "失败"},
    {"摘要说明": "撤销", "交易类型": "未完成"},
    {"摘要说明": "无法办理", "备注": "冲正"},
    {"摘要说明": "", "备注": "失败", "交易类型": "冲正"},
    {"摘要说明": "冲正", "备注": "状态未知"},
    {"摘要说明": "无异常", "备注": "冲正", "交易类型": "失败"},
])
def test_conflicting_or_unknown_semantics_in_another_source_field_prevent_cancellation(changes):
    rows = round_trip()
    rows[2].update(changes)
    plan = parse(rows)
    assert_unmatched(plan)
    assert [record["disposition"] for record in plan["records"]] == ["accepted", "accepted", "unresolved"]


@pytest.mark.parametrize("result_label", ["失败", "未完成", "无法办理", "处理中", "未知状态", "状态未知", "待确认"])
def test_complete_result_labels_still_conflict_when_mixed_with_ordinary_business_descriptions(result_label):
    rows = round_trip()
    rows[2].update({"摘要说明": "冲正", "备注": "POS消费；" + result_label, "交易类型": "银行卡业务"})
    plan = parse(rows)
    assert_unmatched(plan)
    assert [record["disposition"] for record in plan["records"]] == ["accepted", "accepted", "unresolved"]


@pytest.mark.parametrize("changes", [{"交易是否成功": "02"}, {"收付标志": ""}, {"收付标志": "进"},
                                     {"交易时间": "bad"}, {"交易金额": "-10.001"},
                                     {"交易余额": "bad"}, {"交易余额": "100.001"},
                                     {"交易余额": ""}, {"交易余额": "100.01"}, {"交易流水号": ""}])
def test_negative_leg_must_pass_every_other_validation_and_balance_condition(changes):
    rows = round_trip()
    rows[2].update(changes)
    plan = parse(rows)
    assert_unmatched(plan)
    old = parse(rows, profile=feedback.PREVIOUS_PROFILE)
    assert plan["records"] == old["records"] and plan["rows"] == old["rows"]


@pytest.mark.parametrize("position,changes", [
    (0, {"交易余额": ""}), (0, {"交易余额": "bad"}), (0, {"交易余额": "100.001"}),
    (1, {"交易余额": ""}), (1, {"交易余额": "bad"}), (1, {"交易余额": "90.01"}),
    (1, {"交易是否成功": "02"}), (1, {"交易流水号": "syn-pair"}),
    (1, {"交易时间": "2026-01-01 10:02:00"}),
    (0, {"交易时间": "2026-01-01 10:01:00"}),
])
def test_original_and_unique_prior_balance_are_required(position, changes):
    rows = round_trip()
    rows[position].update(changes)
    assert_unmatched(parse(rows))


@pytest.mark.parametrize("time,reference", [("2026-01-01 10:00:00", "SYN-EXTRA"),
                                          ("2026-01-01 10:01:00", "SYN-EXTRA"),
                                          ("2026-01-01 10:02:00", "SYN-EXTRA"),
                                          ("2026-01-01 10:01:30", "SYN-EXTRA"),
                                          ("2026-01-01 09:59:00", "SYN-PAIR")])
def test_time_ties_intervening_rows_and_reference_reuse_do_not_choose_csv_order(time, reference):
    rows = round_trip() + [transaction(**{"交易时间": time, "交易流水号": reference})]
    assert_unmatched(parse(rows))


@pytest.mark.parametrize("changes", [{"交易账号": "SYN-OTHER"}, {"交易账号": "", "交易卡号": "SYN-ACCOUNT"},
                                     {"交易币种": "USD"}])
def test_account_namespace_account_and_currency_do_not_cross_match(changes):
    rows = round_trip()
    rows[2].update(changes)
    assert_unmatched(parse(rows))


def test_member_boundaries_do_not_cross_match():
    rows = round_trip()
    plan = parse(rows[:2], extra=[("synthetic/other.csv", "transaction", rows[2:])])
    assert_unmatched(plan)


@pytest.mark.parametrize("amount", ["", "bad", "2.00", "-2.00", "0.001"])
def test_unlocated_nonzero_or_unknown_transaction_in_scope_blocks_pair(amount):
    rows = round_trip() + [transaction(**{"交易时间": "bad", "交易金额": amount, "交易流水号": "SYN-UNLOCATED"})]
    assert_unmatched(parse(rows))


@pytest.mark.parametrize("changes", [{"交易时间": "bad", "交易金额": "0.00"},
                                     {"交易时间": "", "交易金额": "", "收付标志": ""},
                                     {"交易时间": "bad", "交易金额": "3.00", "交易账号": "SYN-OTHER"}])
def test_zero_feedback_only_and_unrelated_scope_do_not_block_pair(changes):
    rows = round_trip() + [transaction(**{"交易流水号": "SYN-EXTRA", **changes})]
    assert parse(rows)["stats"]["cancelled"] == 2


def test_physical_order_does_not_replace_strict_time_and_decimal_context_is_not_inherited():
    rows = round_trip("进", "入")
    with localcontext() as context:
        context.prec = 2
        plan = parse([rows[2], rows[0], rows[1]])
    assert plan["cancellations"] == [{"original_record_no": 3, "cancellation_record_no": 1, "witness_record_no": 2}]
    assert [row["_source_row"] for row in plan["rows"]] == [2]


def test_unmatched_positive_reversal_text_keeps_v2_acceptance():
    for text in ("冲正", "撤销", "未冲正", "退款", "退货"):
        rows = [transaction(**{"摘要说明": text})]
        plan, old = parse(rows), parse(rows, profile=feedback.PREVIOUS_PROFILE)
        assert plan["records"] == old["records"] and plan["rows"] == old["rows"]
        assert_unmatched(plan)


def test_multiple_pairs_keep_distinct_roles_and_one_identity_conflict_does_not_reinstate_flows():
    rows = round_trip() + [
        transaction(**{"交易时间": "2026-01-01 10:03:00", "交易金额": "20.00",
                       "交易余额": "80.00", "交易流水号": "SYN-SECOND"}),
        transaction(**{"交易时间": "2026-01-01 10:04:00", "交易金额": "-20.00",
                       "交易流水号": "SYN-SECOND", "摘要说明": "撤销"})]
    plan = parse(rows)
    assert plan["cancellations"] == [
        {"original_record_no": 2, "cancellation_record_no": 3, "witness_record_no": 1},
        {"original_record_no": 4, "cancellation_record_no": 5, "witness_record_no": 3}]
    assert plan["stats"]["cancelled"] == 4 and plan["stats"]["ok"] == 1
    assert feedback.revoke_cancellation(plan, 2)
    assert plan["cancellations"] == [{"original_record_no": 4, "cancellation_record_no": 5, "witness_record_no": 3}]
    assert [row["_source_row"] for row in plan["cancelled_rows"]] == [4]
    assert [row["_source_row"] for row in plan["rows"]] == [1]
    assert plan["stats"]["cancelled"] == plan["stats"]["unresolved"] == 2
    assert plan["stats"]["skipped"] == 4


def test_revoking_a_pair_fails_both_legs_closed_and_recomputes_all_stats():
    account = {"交易账号": "SYN-ACCOUNT", "账户开户名称": "Synthetic owner", "账号开户银行": "Synthetic bank"}
    plan = parse(round_trip(), extra=[("synthetic/accounts.csv", "account", [account])])
    old_negative = parse(round_trip(), profile=feedback.PREVIOUS_PROFILE)["records"][2]
    assert feedback.revoke_cancellation(plan, 2) is True
    assert_unmatched(plan)
    original, cancellation = plan["records"][1:3]
    assert original["disposition"] == cancellation["disposition"] == "unresolved"
    assert original["reason_code"] == "own_identity_conflict"
    assert original["details"]["validation_issues"] == "own_identity_conflict"
    assert "own_identity_sha256" not in original["details"] and "own_identity_record" not in original["details"]
    assert original["details"]["canonical_amount"] == "10.00"
    assert cancellation == old_negative
    digest = next(member["sha256"] for member in plan["members"] if member["role"] == "transaction")
    assert original["record_key"] == feedback.record_key("synthetic/transactions.csv", digest, 3)
    assert original["details"]["financial_effect"] == cancellation["details"]["financial_effect"] == "undetermined"
    assert len(plan["rows"]) == 1
    assert plan["stats"]["unresolved"] == plan["stats"]["skipped"] == 2
    assert plan["stats"]["reason_counts"] == {"transaction_valid": 1, "own_identity_conflict": 1,
                                               "negative_reversal_text": 1, "auxiliary_table": 1}
    assert plan["stats"]["currency_totals"] == {"CNY": "1.00"}
    assert [record["reason"] for record in plan["stats"]["bad_examples"]] == ["own_identity_conflict", "negative_reversal_text"]
    assert feedback.revoke_cancellation(plan, 2) is False


def test_bulk_identity_conflicts_revoke_most_of_1000_pairs_with_one_stats_refresh(monkeypatch):
    rows = [transaction()]
    start = datetime(2026, 1, 1, 10)
    for index in range(1000):
        reference = f"SYN-PAIR-{index:04d}"
        rows.extend([
            transaction(**{"交易时间": (start + timedelta(seconds=index * 2 + 1)).strftime("%Y-%m-%d %H:%M:%S"),
                           "交易金额": "10.00", "交易余额": "90.00", "交易流水号": reference}),
            transaction(**{"交易时间": (start + timedelta(seconds=index * 2 + 2)).strftime("%Y-%m-%d %H:%M:%S"),
                           "交易金额": "-10.00", "摘要说明": "冲正", "交易流水号": reference})])
    plan = parse(rows)
    assert len(plan["cancellations"]) == 1000 and plan["stats"]["cancelled"] == 2000
    conflicts = [index * 2 + 2 for index in range(1000) if index % 10]
    retained = [index * 2 + 2 for index in range(1000) if index % 10 == 0]
    refresh = feedback.refresh_stats
    refresh_calls = []

    def counted_refresh(candidate):
        refresh_calls.append(candidate)
        return refresh(candidate)

    monkeypatch.setattr(feedback, "refresh_stats", counted_refresh)
    assert feedback.revoke_cancellations(plan, [*conflicts, *conflicts[:3], 999999, 3]) == 900
    assert len(refresh_calls) == 1
    assert [pair["original_record_no"] for pair in plan["cancellations"]] == retained
    assert [row["_source_row"] for row in plan["cancelled_rows"]] == retained
    assert [row["_source_row"] for row in plan["rows"]] == [1]
    records = {record["record_no"]: record for record in plan["records"]}
    digest = plan["members"][0]["sha256"]
    for original_no in conflicts:
        original, cancellation = records[original_no], records[original_no + 1]
        assert original["reason_code"] == "own_identity_conflict"
        assert original["record_key"] == feedback.record_key("synthetic/transactions.csv", digest, original_no + 1)
        assert "own_identity_sha256" not in original["details"]
        assert cancellation["reason_code"] == "negative_reversal_text"
        for record in (original, cancellation):
            assert record["disposition"] == "unresolved"
            assert record["details"]["financial_effect"] == "undetermined"
            assert not any(key in record["details"] for key in (
                "cancellation_policy", "cancellation_role", "paired_record_no", "cancellation_witness_record_no"))
    for original_no in retained:
        assert records[original_no]["details"]["cancellation_role"] == "original"
        assert "own_identity_sha256" in records[original_no]["details"]
        assert records[original_no + 1]["details"]["cancellation_role"] == "cancellation"
    stats = plan["stats"]
    assert stats["ok"] == 1 and stats["cancelled"] == 200 and stats["unresolved"] == 1800
    assert stats["skipped"] == 2000 and stats["total"] == stats["record_total"] == 2001
    assert sum(stats[key] for key in ("ok", "cancelled", "unresolved", "review", "non_monetary", "non_transaction", "auxiliary")) == stats["record_total"]
    assert stats["reason_counts"] == {"transaction_valid": 1, "own_identity_conflict": 900,
                                      "negative_reversal_text": 900, "matched_cancellation": 200}
    assert sum(stats["reason_counts"].values()) == stats["record_total"]
    assert stats["currency_totals"] == {"CNY": "1.00"}
    assert feedback.revoke_cancellations(plan, conflicts) == 0
    assert feedback.revoke_cancellations(plan, []) == 0
    assert len(refresh_calls) == 1
