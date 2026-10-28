"""Synthetic v5 RMB/AUD admission and portable currency proofs."""
import io
import json
from decimal import Decimal

from fastapi import HTTPException
import pytest

from app import case_packages, feedback_import as feedback
from test_cancellation_import_lifecycle import count, import_version
from test_feedback_cancellations import round_trip
from test_feedback_import_api import archive, complete, preview, transaction
from test_feedback_package import _archive, _members, _mutate


V4 = "funds-trace-bank-feedback-v4"
V5 = "funds-trace-bank-feedback-v5"


def _parse(rows, profile=V5):
    return feedback.parse(io.BytesIO(archive(rows, account=False)), profile=profile)


@pytest.mark.parametrize("currency,canonical", [("RMB", "CNY"), ("AUD", "AUD")])
def test_v5_exact_currency_rules_preserve_source_and_freeze_all_older_profiles(currency, canonical):
    rows = [transaction(**{"交易币种": currency})]
    for version in (feedback.IDENTITY_PROFILE, feedback.PREVIOUS_PROFILE, feedback.CANCELLATION_PROFILE, V4):
        old = _parse(rows, version)
        assert old["rows"] == []
        assert old["records"][0]["reason_code"] == "invalid_currency"
        assert "source_currency" not in old["records"][0]["details"]
    current = _parse(rows)
    assert feedback.PROFILE == V5
    assert current["rows"][0]["currency"] == canonical
    details = current["records"][0]["details"]
    assert details["source_currency"] == currency and details["canonical_currency"] == canonical
    assert details["currency_basis"] == ("literal_rmb_alias" if currency == "RMB" else "literal_iso_code")


@pytest.mark.parametrize("currency", ["rmb", "aud", "cny", "036", "156", "¥", "$", "CAD", "XYZ", ""])
def test_v5_does_not_infer_other_currency_tokens(currency):
    plan = _parse([transaction(**{"交易币种": currency})])
    assert plan["rows"] == [] and plan["records"][0]["reason_code"] == "invalid_currency"
    assert not set(plan["records"][0]["details"]) & {"source_currency", "canonical_currency", "currency_basis"}


@pytest.mark.parametrize("currency", ["CNY", "USD", "EUR", "JPY", "HKD", "GBP"])
def test_v5_keeps_existing_iso_facts_and_occurrence_keys(currency):
    rows = [transaction(**{"交易币种": currency})]
    old, current = _parse(rows, V4), _parse(rows)
    assert old["rows"] == current["rows"] and old["stats"] == current["stats"]
    for record in current["records"]:
        for field in ("source_currency", "canonical_currency", "currency_basis"):
            record["details"].pop(field)
    assert old["records"] == current["records"]


def test_v5_inherits_only_the_closed_text_and_record_allowances(monkeypatch):
    monkeypatch.setattr(feedback, "MAX_RECORDS", 1)
    monkeypatch.setattr(feedback, "TEXT_MAX_RECORDS", 2)
    monkeypatch.setattr(feedback, "PROFILE", feedback.IDENTITY_PROFILE)
    rows = [transaction(**{"交易币种": "RMB", "交易发生地": "Synthetic\x1clocation"})] * 2
    assert len(_parse(rows)["rows"]) == 2
    with pytest.raises(feedback.FeedbackImportError, match="record_count"):
        _parse(rows + rows[:1])
    for changes in ({"交易账号": "Synthetic\x1caccount"}, {"交易发生地": "Synthetic\x00location"}):
        with pytest.raises(feedback.FeedbackImportError, match="binary_member"):
            _parse([transaction(**{"交易币种": "RMB", **changes})])


@pytest.mark.parametrize("currency", ["RMB", "AUD"])
@pytest.mark.parametrize("changes,reason", [({"交易金额": "-2.00"}, "negative_unknown"),
                                            ({"交易是否成功": "02"}, "unknown_status"),
                                            ({"收付标志": ""}, "missing_core")])
def test_new_currency_never_bypasses_existing_financial_vetoes(currency, changes, reason):
    plan = _parse([transaction(**{"交易币种": currency, **changes})])
    assert plan["rows"] == [] and plan["records"][0]["reason_code"] == reason
    assert plan["records"][0]["details"]["source_currency"] == currency


@pytest.mark.parametrize("with_table", [False, True])
def test_v5_public_exact_totals_repeat_original_history_and_format_round_trip(
        client, database, make_case, with_table):
    cid = make_case("Synthetic v5 exact currency totals")
    content = archive([
        transaction(**{"交易流水号": "SYN-RMB", "交易币种": "RMB", "交易金额": "0.10", "交易发生地": "Synthetic\x1clocation"}),
        transaction(**{"交易流水号": "SYN-CNY", "交易币种": "CNY", "交易金额": "0.20"}),
        transaction(**{"交易流水号": "SYN-AUD", "交易币种": "AUD", "交易金额": "90071992547409.91"}),
    ])
    view = preview(client, cid, content)
    assert view["automatic"] and view["feedback"]["profile"] == V5
    assert view["feedback"]["currency_totals"] == {"CNY": "0.30", "AUD": "90071992547409.91"}
    result = complete(client, cid, view)
    assert result["stage"] == "done" and result["result"]["ok"] == 3
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == content
    assert complete(client, cid, preview(client, cid, content))["result"]["rows_dup"] == 3
    assert count(database, "flows", cid) == 3
    history = client.get(f"/api/v1/cases/{cid}/imports").json()["items"]
    assert all(item["result"]["feedback"]["profile"] == V5 for item in history)
    assert client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json()["result"] == result["result"]
    if with_table:
        response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": (
            "synthetic.csv", "交易时间,交易金额,收付标志,交易账号,交易对手账卡号\n2026-01-02 10:00:00,2.00,出,SYN-C,SYN-D\n".encode())})
        assert response.status_code == 200, response.text
        assert complete(client, cid, response.json())["stage"] == "done"
    package = case_packages.export_package(cid)[0]
    assert json.loads(_members(package)["meta.json"])["format"] == 6
    restored = case_packages.import_package(package)["case_id"]
    assert database.q_raw("select currency,sum(amount) amount from effective_flows where case_id=? group by currency order by currency", [restored]) == [
        {"currency": "AUD", "amount": Decimal("90071992547409.91")},
        {"currency": "CNY", "amount": Decimal("2.30" if with_table else "0.30")},
    ]


def test_pinned_v4_ready_rejects_new_codes_and_v5_upgrade_keeps_old_receipt(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic frozen v4 currency ready")
    content = archive([transaction(**{"交易币种": "RMB"}), transaction(**{"交易币种": "AUD", "交易流水号": "SYN-AUD"})])
    with monkeypatch.context() as old:
        old.setattr(feedback, "PROFILE", V4)
        view = preview(client, cid, content)
    assert view["feedback"]["unresolved"] == 2 and view["stats"]["ok"] == 0
    old_job = complete(client, cid, view)
    assert old_job["stage"] == "done" and old_job["result"]["feedback"]["profile"] == V4
    before = database.q_raw("select * from import_records where source_id=? order by record_no", [view["import_id"]])
    current = complete(client, cid, preview(client, cid, content))
    assert current["result"]["ok"] == 2 and current["result"]["feedback"]["profile"] == V5
    assert before == database.q_raw("select * from import_records where source_id=? order by record_no", [view["import_id"]])
    assert client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json()["result"] == old_job["result"]
    assert count(database, "effective_flows", cid) == 2


def _pair(currency):
    return [dict(row, **{"交易币种": currency, "交易账号": "SYN-A", "交易卡号": "SYN-CARD"}) for row in round_trip()]


@pytest.mark.parametrize("currency", ["RMB", "AUD", "CNY"])
def test_v5_cancellations_retain_raw_scope_and_never_revive_under_old_profiles(
        client, database, make_case, monkeypatch, currency):
    cid = make_case("Synthetic v5 currency cancellation")
    content = archive(_pair(currency))
    view, job = import_version(client, cid, content, monkeypatch, V5)
    assert job["result"]["feedback"]["cancelled"] == 2 and job["result"]["ok"] == 1
    for version in (V4, feedback.IDENTITY_PROFILE, V5):
        import_version(client, cid, content, monkeypatch, version)
        assert count(database, "effective_flows", cid) == 1
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert count(database, "effective_flows", restored) == 1


@pytest.mark.parametrize("currency,later", [("CNY", feedback.IDENTITY_PROFILE), ("CNY", V4), ("CNY", V5),
                                            ("RMB", V5), ("AUD", V5)])
def test_v5_cancelled_member_identity_guard_survives_new_and_old_rules(
        client, database, make_case, monkeypatch, currency, later):
    cid = make_case("Synthetic v5 cancellation identity guard")
    import_version(client, cid, archive(_pair(currency)), monkeypatch, V5)
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    view, job = import_version(client, cid, archive(_pair(currency), owner_name="Synthetic changed owner"), monkeypatch, later)
    assert job["result"]["ok"] == 0 and count(database, "effective_flows", cid) == 1
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before
    assert database.q_raw("select reason_code from import_records where source_id=? and record_no=1", [view["import_id"]]) == [
        {"reason_code": "own_identity_conflict"}]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert count(database, "effective_flows", restored) == 1


def test_v4_cancelled_iso_facts_upgrade_to_v5_without_rewriting_old_facts_or_receipts(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic existing cancelled currency")
    content = archive(_pair("CNY"))
    import_version(client, cid, content, monkeypatch, feedback.PREVIOUS_PROFILE)
    old_view, old_job = import_version(client, cid, content, monkeypatch, V4)
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    _, current = import_version(client, cid, content, monkeypatch, V5)
    assert current["result"]["ok"] == 0 and count(database, "effective_flows", cid) == 1
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before
    assert client.get(f"/api/v1/cases/{cid}/imports/{old_view['import_id']}").json()["result"] == old_job["result"]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert count(database, "effective_flows", restored) == 1


@pytest.mark.parametrize("tokens", [("RMB", "RMB", "CNY"), ("CNY", "CNY", "RMB"), ("RMB", "CNY", "CNY")])
def test_v5_does_not_pair_across_raw_rmb_cny_tokens(tokens):
    rows = _pair("CNY")
    for row, token in zip(rows, tokens):
        row["交易币种"] = token
    plan = _parse(rows)
    assert plan["cancellations"] == []
    assert plan["stats"]["cancelled"] == 0
    assert plan["stats"]["currency_totals"] == {"CNY": "11.00"}


@pytest.mark.parametrize("changes", [{"交易是否成功": "02"}, {"收付标志": ""}, {"交易金额": "-2.00"}])
def test_v5_currency_admission_does_not_relax_pair_denials(changes):
    rows = _pair("RMB")
    rows[-1].update(changes)
    plan = _parse(rows)
    assert plan["cancellations"] == [] and plan["stats"]["cancelled"] == 0
    assert plan["stats"]["currency_totals"] == {"CNY": "11.00"}


@pytest.mark.parametrize("change", ["basis", "missing_basis", "canonical", "flow", "retained_flow", "old_profile", "pair_raw", "witness_currency"])
def test_v5_package_rejects_currency_proof_tampering_before_case_creation(
        client, database, make_case, monkeypatch, tmp_path, change):
    cid = make_case("Synthetic currency proof tamper")
    currency = "CNY" if change == "retained_flow" else "RMB"
    if change == "retained_flow":
        import_version(client, cid, archive(_pair(currency)), monkeypatch, feedback.PREVIOUS_PROFILE)
    import_version(client, cid, archive(_pair(currency)), monkeypatch, V5)
    members = _members(case_packages.export_package(cid)[0])
    if change == "flow":
        _mutate(members, "flows", "update entries set currency='AUD'", tmp_path)
    elif change == "retained_flow":
        _mutate(members, "flows", "update entries set currency='AUD' where trade_no='SYN-PAIR'", tmp_path)
    elif change == "old_profile":
        _mutate(members, "import_sources", "update entries set parser_version='funds-trace-bank-feedback-v4'", tmp_path)
    else:
        if change == "witness_currency":
            _mutate(members, "flows", "update entries set currency='AUD' where trade_no='SYN-WITNESS'", tmp_path)
        patch = {
            "basis": ('{"currency_basis":"guessed"}', "record_no=1"),
            "missing_basis": ('{"currency_basis":null}', "record_no=1"),
            "canonical": ('{"canonical_currency":"AUD"}', "record_no=1"),
            "pair_raw": ('{"source_currency":"CNY","canonical_currency":"CNY","currency_basis":"literal_iso_code"}', "record_no=3"),
            "witness_currency": ('{"source_currency":"AUD","canonical_currency":"AUD","currency_basis":"literal_iso_code"}', "record_no=1"),
        }[change]
        _mutate(members, "import_records", "update entries set details_json=json_merge_patch(details_json,'" + patch[0] + "') where " + patch[1], tmp_path)
    before = database.q_raw("select id from cases order by id")
    with pytest.raises(HTTPException):
        case_packages.import_package(_archive(members))
    assert database.q_raw("select id from cases order by id") == before
