"""Synthetic source binding keeps complete facts without full-journal rewrites."""
import copy
import hashlib
import io
import json
from decimal import Decimal

import pytest

from app import evidence_resolution, import_staging as staging, table_archive
from test_folder_evidence import account, archive, complete, preview, table, transaction
from test_table_cancellations import source


def close(plan):
    for key in ("_plan_store", "_details_pool"):
        if plan.get(key) is not None:
            plan[key].close()


def simple_plan(count=19):
    return {"members": [{"member_path": "rows.csv", "sha256": "a" * 64, "role": "table"}],
            "records": [{"record_no": number, "source_row": number + 1,
                         "member_path": "rows.csv", "disposition": "accepted",
                         "details": {"reason": "Synthetic source evidence", "source_row": str(number + 1)}}
                        for number in range(1, count + 1)], "rows": []}


def test_member_family_binding_neither_decodes_nor_rewrites_the_journal(database, monkeypatch):
    monkeypatch.setattr(staging, "PLAN_CHUNK", 2)
    expected = simple_plan()
    actual = staging.stage_plan(copy.deepcopy(expected))
    try:
        held = actual["records"][0]
        nested = held["details"]
        before = actual["_plan_store"].metrics()
        evidence_resolution.bind_families(expected)
        evidence_resolution.bind_families(actual)
        actual["_plan_store"].flush()
        after = actual["_plan_store"].metrics()
        assert after["decode_calls"] == before["decode_calls"]
        assert after["update_encoded_bytes"] == before["update_encoded_bytes"]
        print("synthetic_family_binding_cost", {"extra_decodes": after["decode_calls"] - before["decode_calls"],
                                                "rewrite_bytes": after["update_encoded_bytes"] - before["update_encoded_bytes"]})
        assert nested.copy() == expected["records"][0]["details"]
        assert copy.deepcopy(list(actual["records"])) == expected["records"]
    finally:
        close(actual)


def test_complete_binding_decodes_only_auxiliary_and_paired_financial_rows(database, monkeypatch):
    monkeypatch.setattr(staging, "PLAN_CHUNK", 4)
    original, observed = evidence_resolution.bind_occurrences, []

    def measure(plan):
        store = plan["_plan_store"]
        before = store.metrics()
        result = original(plan)
        store.flush()
        after = store.metrics()
        observed.append((after["decode_calls"] - before["decode_calls"],
                         len(plan["rows"]) + len(plan["cancelled_rows"])))
        return result

    monkeypatch.setattr(evidence_resolution, "bind_occurrences", measure)
    content = archive([
        ("SYN-REQUEST/Synthetic bank交易明细信息.csv", table("transaction", [
            transaction(**{"交易流水号": f"SYN-{number}"}) for number in range(41)])),
        ("account.csv", table("account", [account()])),
        ("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank"}])),
    ])
    plan = table_archive.parse(io.BytesIO(content), profile=table_archive.EVIDENCE_PROFILE, _stage=True)
    try:
        assert len(observed) == 1
        decodes, financial = observed[0]
        # Two auxiliary passes and one row/record pair per financial observation.
        assert decodes <= 2 * 2 + 2 * financial
        print("synthetic_complete_binding_cost", {"decodes": decodes, "financial": financial})
        assert financial == 41
        assert all(row["_occurrence_scope"] == "Synthetic bank" for row in plan["rows"])
    finally:
        close(plan)


def test_family_rebinding_preserves_old_aliases_explicit_edits_and_deleted_fields(database, monkeypatch):
    monkeypatch.setattr(staging, "PLAN_CHUNK", 1)
    expected = simple_plan(5)
    actual = staging.stage_plan(copy.deepcopy(expected))
    store = actual["_plan_store"]
    try:
        nested = actual["records"][0]["details"]
        evidence_resolution.bind_families(expected)
        evidence_resolution.bind_families(actual)
        nested["evidence_family"] = "Synthetic explicit late edit"
        del actual["records"][1]["details"]["family_member_sha256"]
        store.flush()
        # Read beyond the cache, while retaining only the first nested alias.
        for _record in actual["records"]:
            pass
        assert actual["records"][0]["details"]["evidence_family"] == "Synthetic explicit late edit"
        assert "family_member_sha256" not in actual["records"][1]["details"]
        extra = {"member_path": "extra.csv", "sha256": "b" * 64, "role": "auxiliary"}
        for plan in (expected, actual):
            plan["members"].append(extra)
            evidence_resolution.bind_families(plan)
        assert nested.copy() == expected["records"][0]["details"]
        nested["reason"] = "Synthetic nested late update"
        expected["records"][0]["details"]["reason"] = "Synthetic nested late update"
        store.flush()
        assert copy.deepcopy(list(actual["records"])) == expected["records"]
    finally:
        close(actual)
    with pytest.raises(ValueError, match="closed"):
        nested["reason"] = "forbidden"
    with pytest.raises(ValueError, match="closed"):
        evidence_resolution.bind_families(actual)


def test_auxiliary_selection_tracks_flushed_classification_without_decoding_other_rows(database, monkeypatch):
    monkeypatch.setattr(staging, "PLAN_CHUNK", 1)
    plan = simple_plan(23)
    plan["records"][2]["disposition"] = "auxiliary"
    actual = staging.stage_plan(plan)
    store = actual["_plan_store"]
    try:
        old = actual["records"][2]
        replacement = actual["records"][17]
        old["disposition"] = "unresolved"
        replacement["disposition"] = "auxiliary"
        # The selector itself flushes pending classification changes.
        selected = list(staging.auxiliary_records(actual))
        assert [row["record_no"] for row in selected] == [18]
        del selected, old, replacement
        for _row in actual["records"]:
            pass
        before = store.metrics()
        assert [row["record_no"] for row in staging.auxiliary_records(actual)] == [18]
        after = store.metrics()
        assert after["decode_calls"] - before["decode_calls"] == 1
        assert after["peak_read_records"] <= 1
    finally:
        close(actual)
    with pytest.raises(ValueError, match="closed"):
        list(staging.auxiliary_records(actual))


def test_member_notes_keep_cache_budget_and_do_not_bind_later_appends_early(database, monkeypatch):
    monkeypatch.setattr(staging, "PLAN_CHUNK_BYTES", 1000)
    plan = staging.stage_plan(simple_plan(9))
    store = plan["_plan_store"]
    try:
        evidence_resolution.bind_families(plan)
        for record in plan["records"]:
            assert record["details"]["evidence_family"]
            assert store._read_bytes <= staging.PLAN_CHUNK_BYTES
        extra = {"record_no": 10, "source_row": 11, "member_path": "rows.csv",
                 "disposition": "auxiliary", "details": {"reason": "Synthetic later append"}}
        plan["records"].append(extra)
        assert plan["records"][9]["details"].copy() == extra["details"]
        copied = plan["records"][0]
        copied["details"]["evidence_family"] = "Synthetic explicit copied edit"
        del copied["details"]["family_member_sha256"]
        plan["records"].append(copied)
        assert plan["records"][10]["details"].copy() == copied["details"].copy()
        evidence_resolution.bind_families(plan)
        assert plan["records"][9]["details"]["evidence_family"]
        assert plan["records"][10]["details"]["evidence_family"] != "Synthetic explicit copied edit"
    finally:
        close(plan)


def test_paired_rows_preserve_order_sparse_sources_cancelled_entries_and_late_updates(database, monkeypatch):
    monkeypatch.setattr(staging, "PLAN_CHUNK", 1)
    plan = simple_plan(7)
    plan["rows"] = [{"_source_row": 5}, {"_source_row": 1}]
    plan["cancelled_rows"] = [{"_source_row": 7}, {"_source_row": 3}]
    actual = staging.stage_plan(plan)
    store = actual["_plan_store"]
    try:
        row = actual["rows"][0]
        row["_source_row"] = 6
        pairs = []
        for row, record in staging.row_records(actual):
            pairs.append((row["_source_row"], record["record_no"]))
            record["details"]["paired_probe"] = str(row["_source_row"])
        assert pairs == [(6, 6), (1, 1), (7, 7), (3, 3)]
        store.flush()
        assert actual["records"][5]["details"]["paired_probe"] == "6"
        actual["rows"][0]["_source_row"] = 999
        with pytest.raises(KeyError):
            list(staging.row_records(actual))
    finally:
        close(actual)


def test_public_complete_import_persists_independent_family_and_money_expectations(client, database, make_case):
    members = [
        ("SYN-REQUEST/Synthetic bank交易明细信息.csv", source([
            transaction(**{"交易流水号": "SYN-1", "交易时间": "2026-01-01 10:00:00.123456"}),
            transaction(**{"交易流水号": "SYN-2", "交易金额": "20.03", "交易币种": "USD"})])[0]),
        ("account.csv", table("account", [account()])),
        ("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank"}])),
    ]
    cid = make_case("Synthetic bounded source binding")
    view = preview(client, cid, [("nested.zip", archive(members))], scope_bank=None)
    assert view["table_summary"]["accepted"] == 2 and view["table_summary"]["auxiliary"] == 2
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert job["result"]["ok"] == 2
    flows = database.q_raw("select trade_no,amount,currency,trade_time from flows where case_id=? order by trade_no", [cid])
    assert [(row["trade_no"], row["amount"], row["currency"]) for row in flows] == [
        ("SYN-1", Decimal("10.25"), "CNY"), ("SYN-2", Decimal("20.03"), "USD")]
    assert flows[0]["trade_time"].microsecond == 123456
    identities = sorted((path, hashlib.sha256(content).hexdigest(), "table" if index == 0 else "auxiliary")
                        for index, (path, content) in enumerate(members))
    expected_family = hashlib.sha256(json.dumps(["logical-source-family-v1", identities], ensure_ascii=False,
                                               separators=(",", ":")).encode()).hexdigest()
    journal = database.q_raw("select member_path,details_json from import_records where case_id=? order by record_no", [cid])
    assert len(journal) == 4
    for record in journal:
        details = json.loads(record["details_json"])
        assert details["evidence_family"] == expected_family
        within = record["member_path"].removeprefix("nested.zip/")
        assert details["family_member_sha256"] == hashlib.sha256(within.encode()).hexdigest()
        if within == members[0][0]:
            assert details["occurrence_scope"] == "Synthetic bank"
