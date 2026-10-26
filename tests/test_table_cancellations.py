"""Synthetic 32-column archive cancellation closure and persisted proofs."""
import copy
import csv
from decimal import Decimal, localcontext
import io
import zipfile

import pytest

from app import table_archive, table_import

COLUMNS = tuple("交易卡号 交易账号 查询反馈结果原因 交易户名 交易证件号码 交易时间 交易金额 交易余额 收付标志 交易对手账卡号 现金标志 对手户名 对手身份证号 对手开户银行 摘要说明 交易币种 交易网点名称 交易发生地 交易是否成功 传票号 IP地址 MAC地址 对手交易余额 交易流水号 日志号 凭证种类 凭证号 交易柜员号 备注 商户名称 商户代码 交易类型".split())


def transaction(**changes):
    row = dict.fromkeys(COLUMNS, "")
    row.update({"交易账号": "SYN-OWN", "交易户名": "Synthetic owner", "交易证件号码": "SYN-ID",
                "交易时间": "2026-01-01 10:00:00", "交易金额": "1.00", "交易余额": "100.00",
                "收付标志": "出", "交易币种": "CNY", "交易是否成功": "01", "交易流水号": "SYN-WITNESS",
                "交易对手账卡号": "SYN-COUNTER", "对手身份证号": "SYN-COUNTER-ID", "商户代码": "SYN-MERCHANT"})
    row.update(changes)
    return row


def round_trip():
    return [transaction(), transaction(**{"交易时间": "2026-01-01 10:01:00", "交易金额": "10.00", "交易余额": "90.00", "交易流水号": "SYN-PAIR"}),
            transaction(**{"交易时间": "2026-01-01 10:02:00", "交易金额": "-10.00", "摘要说明": "冲正", "交易流水号": "SYN-PAIR"})]


def source(rows, *, header=COLUMNS, tail=(), path="synthetic.csv"):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(header)
    writer.writerows([row.get(column, "") for column in header] for row in rows)
    writer.writerows(tail)
    payload = stream.getvalue().encode("utf-8-sig")
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(path, payload)
    return payload, package.getvalue()


def parse(rows, **kwargs):
    profile = kwargs.pop("profile", table_archive.PROFILE)
    return table_archive.parse(io.BytesIO(source(rows, **kwargs)[1]), profile=profile)


@pytest.mark.parametrize("profile", sorted(table_archive.CANCELLATION_PROFILES))
def test_pair_retains_records_and_only_excludes_effective_funds(profile):
    from app import evidence_resolution, table_cancellations as policy
    plan = parse(round_trip(), profile=profile)
    assert plan["profile"] == profile
    assert plan["cancellations"] == [{"original_record_no": 2, "cancellation_record_no": 3, "witness_record_no": 1}]
    assert [row["_source_row"] for row in plan["rows"]] == [1]
    assert [row["_source_row"] for row in plan["cancelled_rows"]] == [2]
    assert plan["stats"]["cancelled"] == 2 and plan["stats"]["accepted"] == 1
    assert plan["stats"]["currency_totals"] == {"CNY": "1.00"}
    assert policy.validate_evidence(plan["records"]) == plan["cancellations"]
    if profile in table_archive.EVIDENCE_PROFILES:
        assert all(evidence_resolution.DETAIL_FIELD in record["details"] for record in plan["records"][:2])
    for record in plan["records"]:
        notes = dict(record["details"])
        if profile in table_archive.EVIDENCE_PROFILES and evidence_resolution.DETAIL_FIELD in notes:
            evidence = evidence_resolution.validate_evidence(notes.pop(evidence_resolution.DETAIL_FIELD))
            assert evidence["role"] == "transaction"
            assert evidence["endpoints"]["self"]["account"] == "SYN-OWN"
            assert evidence["endpoints"]["self"]["name"] == "Synthetic owner"
            assert all(set(item) == {"account", "card", "identifier_type", "bank", "name"}
                       for item in evidence["endpoints"].values())
            assert not any(value in str(evidence) for value in ("SYN-ID", "SYN-PAIR"))
        else:
            assert evidence_resolution.DETAIL_FIELD not in notes
        assert notes["cancellation_policy"] == policy.POLICY
        assert not any(value in str(notes) for value in ("SYN-OWN", "SYN-ID", "SYN-PAIR", "Synthetic owner"))
    for record in plan["records"][1:]:
        assert record["disposition"] == "cancelled" and record["reason_code"] == "matched_cancellation"
        assert record["details"]["financial_effect"] == "analysis_excluded"
    assert plan["records"][1]["details"]["flow_sha256"] == table_import.flow_digest(plan["cancelled_rows"][0])


@pytest.mark.parametrize("changes", [
    {"摘要说明": "退款冲正"}, {"摘要说明": "冲正", "备注": "退款"}, {"交易类型": "退货"},
    {"备注": "退税"}, {"备注": "失败"}, {"备注": "待定"}, {"备注": "处理中"},
    {"备注": "未完成"}, {"备注": "不成功"}, {"交易是否成功": "成功"},
    {"交易余额": ""}, {"收付标志": "入"}, {"交易户名": "OTHER"},
    {"交易证件号码": "OTHER"}, {"交易对手账卡号": "OTHER"}, {"商户代码": "OTHER"},
    {"交易币种": ""}, {"交易流水号": "", "凭证号": "SYN-PAIR"},
    {"交易时间": ""}, {"交易金额": "-10.001"},
])
def test_ambiguous_or_conflicting_negative_stays_unresolved(changes):
    rows = round_trip()
    rows[2].update(changes)
    plan = parse(rows)
    assert plan["cancellations"] == [] and plan["stats"]["cancelled"] == 0
    assert plan["records"][2]["disposition"] == "unresolved"


@pytest.mark.parametrize("kind", ["third_reference", "equal_time", "missing_time", "bad_width", "mixed_header", "unlocated_unknown"])
def test_complete_member_index_blocks_hidden_ambiguity(kind):
    rows = round_trip()
    tail = ()
    if kind == "third_reference":
        rows.append(transaction(**{"交易时间": "2026-01-01 10:03:00", "交易流水号": "SYN-PAIR"}))
    elif kind == "equal_time":
        rows.append(transaction(**{"交易时间": rows[1]["交易时间"]}))
    elif kind == "missing_time":
        rows.append(transaction(**{"交易时间": ""}))
    elif kind == "bad_width":
        tail = [("unexpected",)]
    elif kind == "mixed_header":
        tail = [("交易账号", "交易时间", "交易金额")]
    else:
        rows.append(dict.fromkeys(COLUMNS, "") | {"摘要说明": "Unknown source record"})
    plan = parse(rows, tail=tail)
    assert plan["cancellations"] == []
    assert plan["records"][2]["reason_code"] != "matched_cancellation"


def test_explicit_feedback_and_exact_repeated_header_are_known_non_movements():
    rows = round_trip() + [dict.fromkeys(COLUMNS, "") | {"查询反馈结果原因": "Synthetic feedback"}]
    plan = parse(rows, tail=[COLUMNS])
    assert len(plan["cancellations"]) == 1
    from app import table_cancellations as policy
    assert policy.validate_evidence(plan["records"]) == plan["cancellations"]


def test_forward_proved_chain_requires_a_trusted_root():
    rows = round_trip()
    rows += [transaction(**{"交易时间": "2026-01-01 10:03:00", "交易金额": "20.00", "交易余额": "80.00", "交易流水号": "SYN-NEXT"}),
             transaction(**{"交易时间": "2026-01-01 10:04:00", "交易金额": "-20.00", "摘要说明": "撤销", "交易流水号": "SYN-NEXT"})]
    plan = parse(rows)
    assert len(plan["cancellations"]) == 2
    from app import table_cancellations as policy
    assert policy.validate_evidence(plan["records"]) == plan["cancellations"]
    rows[0]["交易金额"] = "-1.00"
    assert parse(rows)["cancellations"] == []


def test_old_profiles_and_physical_identity_remain_frozen():
    rows = round_trip()
    old = [parse(rows, profile=profile) for profile in ("funds-trace-table-archive-v1", "funds-trace-table-archive-v2")]
    current = parse(rows)
    assert table_archive.IDENTITY_PROFILE == "funds-trace-table-archive-v1"
    assert table_archive.PREVIOUS_PROFILE == table_archive.REFERENCE_PROFILE == "funds-trace-table-archive-v2"
    assert [r["record_key"] for r in current["records"]] == [r["record_key"] for r in old[0]["records"]]
    assert all(plan["records"][1]["disposition"] == "accepted" and plan["records"][2]["reason_code"] == "negative_amount" for plan in old)
    flat = table_import.parse(io.BytesIO(source(rows)[0]), suffix=".csv",
                              reference_policy=table_import.TRANSACTION_REFERENCE_POLICY,
                              profile="funds-trace-table-v5")
    assert flat["profile"] == "funds-trace-table-v5" and len(flat["rows"]) == 2


def test_decimal_closure_does_not_use_ambient_precision():
    rows = round_trip()
    for row, balance in zip(rows, ("9999999999999999.99", "9999999999999989.99", "9999999999999999.99")):
        row["交易余额"] = balance
    with localcontext() as context:
        context.prec = 6
        assert len(parse(rows)["cancellations"]) == 1


@pytest.mark.parametrize("field,value", [("cancellation_balance", "99.00"), ("cancellation_reference_sha256", "a" * 64),
                                         ("cancellation_scope_sha256", "b" * 64), ("cancellation_quality", "other")])
def test_persisted_proof_rejects_tampering(field, value):
    from app import table_cancellations as policy
    records = copy.deepcopy(parse(round_trip())["records"])
    records[2]["details"][field] = value
    with pytest.raises(ValueError):
        policy.validate_evidence(records)


def test_revocation_removes_dependent_chain_and_preserves_independent_pair():
    from app import table_cancellations as policy
    rows = round_trip()
    rows += [transaction(**{"交易时间": "2026-01-01 10:03:00", "交易金额": "20.00", "交易余额": "80.00", "交易流水号": "SYN-NEXT"}),
             transaction(**{"交易时间": "2026-01-01 10:04:00", "交易金额": "-20.00", "摘要说明": "撤销", "交易流水号": "SYN-NEXT"})]
    rows += [row | {"交易账号": "SYN-OTHER"} for row in round_trip()]
    plan = parse(rows)
    assert len(plan["cancellations"]) == 3
    policy.revoke_pairs(plan, {2})
    assert plan["cancellations"] == [{"original_record_no": 7, "cancellation_record_no": 8, "witness_record_no": 6}]
    assert policy.validate_evidence(plan["records"]) == plan["cancellations"]
    for record in plan["records"][1:5]:
        assert record["reason_code"] == "cancellation_historical_conflict"
        assert record["disposition"] == "unresolved"
        assert record["details"]["validation_issues"].split(",")[0] == record["reason_code"]
        assert "flow_sha256" not in record["details"]
        assert not record["details"].keys() & policy.PAIR_FIELDS
    for record in (plan["records"][2], plan["records"][4]):
        assert record["details"]["validation_issues"] == "cancellation_historical_conflict,negative_amount"
    assert [row["_source_row"] for row in plan["rows"]] == [1, 6]
    policy.revoke_pairs(plan, {7})
    assert policy.validate_evidence(plan["records"]) == []
    assert all(not record["details"].keys() & policy.DETAILS_FIELDS for record in plan["records"])


def test_zero_amount_validated_witness_and_source_account_namespace():
    from app import table_cancellations as policy
    rows = round_trip()
    rows[0]["交易金额"] = "0"
    plan = parse(rows)
    assert len(plan["cancellations"]) == 1
    assert plan["records"][0]["details"]["cancellation_quality"] == "valid_zero"
    assert "flow_sha256" not in plan["records"][0]["details"]
    assert policy.validate_evidence(plan["records"]) == plan["cancellations"]
    rows[2]["交易账号"] = ""
    rows[2]["交易卡号"] = "SYN-OWN"
    assert parse(rows)["cancellations"] == []


def test_member_remapping_keeps_evidence_links_and_physical_keys():
    from app import table_cancellations as policy
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("first.csv", source(round_trip())[0])
        archive.writestr("second.csv", source(round_trip())[0])
    plan = table_archive.parse(io.BytesIO(data.getvalue()))
    assert plan["cancellations"] == [
        {"original_record_no": 2, "cancellation_record_no": 3, "witness_record_no": 1},
        {"original_record_no": 5, "cancellation_record_no": 6, "witness_record_no": 4}]
    assert [row["_source_row"] for row in plan["cancelled_rows"]] == [2, 5]
    assert [row["_record_key"] for row in plan["cancelled_rows"]] == [plan["records"][1]["record_key"], plan["records"][4]["record_key"]]
    assert policy.validate_evidence(plan["records"]) == plan["cancellations"]


def test_nonselected_observation_still_agrees_with_its_source_disposition():
    from app import table_cancellations as policy
    rows = round_trip() + [transaction(**{"交易时间": "2026-01-01 10:03:00", "交易流水号": "SYN-LATER"})]
    records = parse(rows)["records"]
    assert records[3]["details"].keys() & policy.CRITICAL_FIELDS == set()
    records[3]["details"]["cancellation_movement"] = "unknown"
    with pytest.raises(ValueError):
        policy.validate_evidence(records)


def test_reordered_exact_region_uses_its_own_column_positions():
    rows = round_trip()
    reordered = tuple(reversed(COLUMNS))
    tail = [reordered] + [[row[column] for column in reordered] for row in rows[1:]]
    plan = parse(rows[:1], tail=tail)
    assert plan["cancellations"] == [{"original_record_no": 3, "cancellation_record_no": 4, "witness_record_no": 1}]
    from app import table_cancellations as policy
    assert policy.validate_evidence(plan["records"]) == plan["cancellations"]


@pytest.mark.parametrize("kind", ["original_empty_status", "witness_empty_status", "zero_witness_empty_status",
                                  "unmatched_refund", "partial_pair"])
def test_unmatched_negative_parse_retains_closed_reasons(kind):
    from app import table_cancellations as policy
    rows = round_trip()
    if kind == "original_empty_status":
        rows[1]["交易是否成功"] = ""
    elif kind in {"witness_empty_status", "zero_witness_empty_status"}:
        rows[0]["交易是否成功"] = ""
        if kind == "zero_witness_empty_status":
            rows[0]["交易金额"] = "0.00"
    else:
        rows[2]["备注"] = "退款"
    if kind == "partial_pair":
        rows += [row | {"交易账号": "SYN-INDEPENDENT"} for row in round_trip()]
    plan = parse(rows)
    assert plan["stats"]["cancelled"] == (2 if kind == "partial_pair" else 0)
    negative = plan["records"][2]
    assert negative["disposition"] == "unresolved"
    assert negative["details"]["validation_issues"].split(",")[0] == negative["reason_code"]
    assert "negative_amount" in negative["details"]["validation_issues"].split(",")[1:]
    assert policy.validate_evidence(plan["records"]) == plan["cancellations"]
    for profile in (table_archive.IDENTITY_PROFILE, table_archive.REFERENCE_PROFILE):
        old = parse(rows, profile=profile)
        assert old["records"][2]["reason_code"] == "negative_amount"
        if "empty_status" in kind:
            status_index = 1 if kind == "original_empty_status" else 0
            assert old["records"][status_index]["reason_code"] == "unknown_status"


@pytest.mark.parametrize("reordered_region", [False, True])
def test_exact_32_column_headers_and_values_follow_existing_text_whitespace_rules(reordered_region):
    from app import table_cancellations as policy
    rows = round_trip()
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL)
    padded = lambda values: [" \t" + value + "\t " for value in values]
    writer.writerow(padded(COLUMNS))
    writer.writerow(padded([rows[0][column] for column in COLUMNS]))
    columns = COLUMNS
    if reordered_region:
        columns = tuple(reversed(COLUMNS))
        writer.writerow(padded(columns))
    writer.writerows(padded([row[column] for column in columns]) for row in rows[1:])
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("synthetic-padded.csv", buffer.getvalue().encode("utf-8-sig"))
    plan = table_archive.parse(io.BytesIO(archive.getvalue()))
    assert plan["stats"]["cancelled"] == 2
    assert plan["stats"]["currency_totals"] == {"CNY": "1.00"}
    assert policy.validate_evidence(plan["records"]) == plan["cancellations"]
    expected_original = 3 if reordered_region else 2
    assert plan["cancellations"] == [{"original_record_no": expected_original,
                                       "cancellation_record_no": expected_original + 1, "witness_record_no": 1}]
    assert all(row["self_account"] == "SYN-OWN" for row in (*plan["rows"], *plan["cancelled_rows"]))


@pytest.mark.parametrize("profile", sorted(table_archive.SUPPORTED_PROFILES))
def test_negative_explicit_failure_preserves_frozen_non_transaction_disposition(profile):
    rows = round_trip()
    rows[2]["交易是否成功"] = "失败"
    plan = parse(rows, profile=profile)
    record = plan["records"][2]
    assert record["disposition"] == "non_transaction"
    assert record["reason_code"] == "failed_transaction"
    assert record["details"]["financial_effect"] == "not_a_flow"
    assert record["details"]["reported_status"] == "explicit_failure"
    assert plan["stats"]["non_transaction"] == 1


def test_failed_negative_still_contributes_full_member_reference_observation():
    from app import table_cancellations as policy
    failed = transaction(**{"交易时间": "2026-01-01 10:03:00", "交易金额": "-20.00",
                            "交易流水号": "SYN-FAILED", "交易是否成功": "失败"})
    plan = parse(round_trip() + [failed])
    assert len(plan["cancellations"]) == 1
    record = plan["records"][3]
    assert (record["disposition"], record["reason_code"]) == ("non_transaction", "failed_transaction")
    assert record["details"]["cancellation_movement"] == "negative"
    assert policy.validate_evidence(plan["records"]) == plan["cancellations"]
    failed["交易流水号"] = "SYN-PAIR"
    blocked = parse(round_trip() + [failed])
    assert blocked["cancellations"] == []
    assert blocked["records"][2]["reason_code"] == "cancellation_reference_not_unique"
    assert (blocked["records"][3]["disposition"], blocked["records"][3]["reason_code"]) == ("non_transaction", "failed_transaction")
