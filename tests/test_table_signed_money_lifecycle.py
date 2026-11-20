"""Synthetic upload, commit and replay of changed monetary spellings."""
import pytest

from app import import_preview_cache
from app.services import imports
from test_automatic_table_import import preview
from test_event_import import csv_rows, transaction
from test_feedback31_reference import COLUMNS as BANK31_COLUMNS, payload as bank_csv, row as bank_row
from test_feedback_import_api import complete
from test_feedback_package import _archive, _members, _mutate
from test_folder_evidence import archive
from test_source_sign_declarations import HEAD, STATEMENT, raw, row
from test_table_cancellations import round_trip, source
from xls_fixtures import workbook


def _flows(client, case):
    response = client.get(f"/api/v1/cases/{case}/flows", params={"granularity": "observations"})
    assert response.status_code == 200, response.text
    return {(flow["payer_account"], flow["payee_account"], flow["amount"], flow["currency"])
            for flow in response.json()["items"]}


def _records(client, case, view):
    response = client.get(f"/api/v1/cases/{case}/imports/{view['import_id']}/records")
    assert response.status_code == 200, response.text
    return response.json()["items"]


@pytest.mark.parametrize("filename,member,profile", [
    ("ordinary.csv", "table.csv", "funds-trace-table-v84"),
    ("synthetic.zip", "statement.csv", "funds-trace-table-archive-v69"),
])
def test_signed_amount_upload_commit_reopen_and_package(client, database, make_case, filename, member, profile):
    case = make_case("Synthetic signed amounts")
    tokens = ("−12.30", "－25.00", "(¥3.05)", "(¥-12.30)", "−4.00")
    directions = ("", "出", "", "", "入")
    reasons = ["transaction_valid"] * 3 + ["invalid_amount", "conflicting_direction"]
    content = raw([[*row(value, STATEMENT, direction), "CNY"]
                   for value, direction in zip(tokens, directions)], [*HEAD, "交易币种"])
    payload = archive([(member, content)]) if filename.endswith(".zip") else content
    view = preview(client, case, payload, filename)
    summary = view["table_summary"]
    assert summary["reason_counts"] == {"transaction_valid": 3, "invalid_amount": 1, "conflicting_direction": 1}
    assert summary["currency_totals"] == {"CNY": "40.35"}
    assert summary["profile"] == profile
    import_preview_cache.discard(view["import_id"])
    job = complete(client, case, view)
    assert (job["stage"], job["result"]["ok"]) == ("done", 3), job
    assert job["result"]["table_summary"]["profile"] == profile
    with database._lock:
        database._con.close()
        database._con = None
    facts = {("SYN-A", "SYN-B", amount, "CNY") for amount in ("12.30", "25.00", "3.05")}
    assert _flows(client, case) == facts
    base = f"/api/v1/cases/{case}"
    flow_ids = [flow["id"] for flow in client.get(base + "/flows").json()["items"]]
    assert client.post("/api/track", json={"case_id": case, "flow_ids": flow_ids}).status_code == 200
    edge, = client.get("/api/graph", params={"case_id": case}).json()["edges"]
    assert (edge["source"], edge["target"], edge["cnt"], edge["total"]) == ("SYN-A", "SYN-B", 3, "40.35")
    stats = client.get(base + "/stats").json()["import_coverage"]
    assert stats["unresolved_records"] == 2
    records = _records(client, case, view)
    assert [(record["source_row"], record["reason_code"], record["flow_id"] is not None)
            for record in records] == [(index, reason, index < 5) for index, reason in enumerate(reasons, 2)]
    review = client.get(f"{base}/imports/{view['import_id']}/review", params={"member_path": member, "view": "all"})
    assert review.status_code == 200, review.text
    assert review.json()["original_error"] is None
    assert [next(cell["value"] for cell in item["original"] if cell["header"] == "交易金额")
            for item in review.json()["items"]] == list(tokens)
    assert client.get(f"{base}/sources/{view['import_id']}/file").content == payload
    exported = client.get(f"/api/cases/{case}/export")
    assert exported.status_code == 200, exported.text
    restored = client.post("/api/cases/import", files={"file": ("source.fundcase", exported.content)})
    assert restored.status_code == 200, restored.text
    new_case = restored.json()["case_id"]
    assert _flows(client, new_case) == facts
    restored_sources = database.q_raw("select source_id,parser_version from import_sources where case_id=?", [new_case])
    restored_source, = restored_sources
    assert restored_source["parser_version"] == profile
    assert [item["details"] for item in _records(client, new_case, {"import_id": restored_source["source_id"]})] == [
        item["details"] for item in records]


def test_signed_xls_cents_and_direction_authority_are_independent(client, make_case):
    case = make_case("Synthetic signed XLS cents")
    headers = ("date", "account", "counterparty account", "signed amount(分)", "direction", "currency")
    date = "2026-01-01 10:00:00"
    content = workbook([("Ledger", [headers,
        (date, "SYN-A", "SYN-B", "－1230", "out", "CNY"),
        (date, "SYN-A", "SYN-B", "−250", "", "USD"),
        (date, "SYN-A", "SYN-B", "−100", "in", "USD"),
        (date, "SYN-A", "SYN-B", "−1.5", "out", "USD"),
    ])])
    view = preview(client, case, content, "synthetic.xls")
    assert view["table_summary"]["reason_counts"] == {"transaction_valid": 2, "conflicting_direction": 1, "amount_precision": 1}
    assert view["table_summary"]["profile"] == "funds-trace-table-v85"
    assert complete(client, case, view)["result"]["ok"] == 2
    assert _flows(client, case) == {("SYN-A", "SYN-B", "12.30", "CNY"), ("SYN-A", "SYN-B", "2.50", "USD")}


def test_signed_evidence_profile_keeps_explicit_event_proof(client, make_case):
    case = make_case("Synthetic signed event evidence")
    content = csv_rows([transaction(own, **{"交易金额": "100.00e−0"}) for own in ("SYN-A", "SYN-B")])
    view = preview(client, case, content)
    assert view["table_summary"]["currency_totals"] == {"CNY": "200.00"}
    assert view["table_summary"]["profile"] == "funds-trace-table-v86"
    assert complete(client, case, view)["result"]["ok"] == 2
    funds = client.get(f"/api/v1/cases/{case}/stats").json()["funds"]
    assert (funds["observation_count"], funds["confirmed_event_count"]) == (2, 1)
    assert funds["totals"] == [{"currency": "CNY", "amount": "100.00", "lower": "100.00", "upper": "100.00"}]


@pytest.mark.parametrize("token,cancelled,unresolved", [("−10.00", 2, 0), ("(¥-10.00)", 0, 1)])
def test_archive_rechecks_cancellation_amount_with_same_grammar(client, make_case, token, cancelled, unresolved):
    case = make_case("Synthetic archived reversal")
    rows = round_trip()
    rows[-1]["交易金额"] = token
    _, payload = source(rows)
    view = preview(client, case, payload, "reversal.zip")
    summary = view["table_summary"]
    assert (summary["cancelled"], summary["unresolved"]) == (cancelled, unresolved)
    assert summary["profile"] == "funds-trace-table-archive-v69"
    assert complete(client, case, view)["result"]["ok"] == (1 if cancelled else 2)
    records = _records(client, case, view)
    assert records[-1]["reason_code"] == ("matched_cancellation" if cancelled else "invalid_amount")
    stats = client.get(f"/api/v1/cases/{case}/stats").json()
    assert stats["amount_total"] == ("1.00" if cancelled else "11.00")
    assert stats["import_coverage"]["cancelled_records"] == cancelled
    assert stats["import_coverage"]["unresolved_records"] == unresolved
    if cancelled:
        assert {amount for _, _, amount, _ in _flows(client, case)} == {"-10.00", "1.00", "10.00"}
    else:
        assert {amount for _, _, amount, _ in _flows(client, case)} == {"1.00", "10.00"}


def test_plain_and_frozen_uploads_keep_v69(client, make_case, monkeypatch):
    case = make_case("Synthetic frozen sign interpretation")
    ordinary = raw([row("-12.30", STATEMENT), row("(¥4.00)", STATEMENT)])
    view = preview(client, case, ordinary)
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"
    assert view["table_summary"]["currency_totals"] == {"CNY": "16.30"}
    memo = preview(client, case, raw([row("12.30", "合成备注：−12.30", "出")]))
    assert memo["table_summary"]["profile"] == "funds-trace-table-v69"

    content = raw([row("−12.30", STATEMENT), row("(¥-12.30)", STATEMENT)])
    parse = imports._parse
    with monkeypatch.context() as frozen:
        frozen.setattr(imports, "_parse", lambda *args, **options: parse(
            *args, **{**options, "table_profile": "funds-trace-table-v69", "_auto_event_profile": False}))
        old = preview(client, case, content)
    import_preview_cache.discard(old["import_id"])
    url = f"/api/v1/cases/{case}/imports/{old['import_id']}"
    reopened = client.post(url + "/preview", json={"mode": old["mode"], "mapping": old["mapping"]})
    assert reopened.status_code == 200, reopened.text
    view = reopened.json()
    assert view["table_summary"] == old["table_summary"]
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"
    assert view["table_summary"]["reason_counts"] == {"invalid_amount": 1, "transaction_valid": 1}
    job = complete(client, case, view)
    assert (job["stage"], job["result"]["ok"]) == ("done", 1), job
    assert _flows(client, case) == {("SYN-B", "SYN-A", "12.30", "CNY")}
    assert client.get(url).json()["result"] == job["result"]


@pytest.mark.parametrize("filename,profile", [
    ("bank.csv", "funds-trace-table-v81"),
    ("bank.xls", "funds-trace-table-v82"),
    ("bank.zip", "funds-trace-table-archive-v71"),
])
def test_registered_bank_profiles_keep_units_and_do_not_infer_negative_direction(client, make_case, filename, profile):
    case = make_case("Synthetic registered bank sign and unit")
    fields = (*BANK31_COLUMNS, "金额单位")
    rows = [
        bank_row(**{"交易金额": "250", "金额单位": "分", "交易余额": "－20.00", "交易币种": "USD"}),
        bank_row(**{"交易金额": "−125", "金额单位": "分", "交易流水号": "SYN-NEGATIVE", "交易币种": "USD"}),
        bank_row(**{"交易金额": "(¥-12.30)", "交易流水号": "SYN-INVALID", "交易币种": ""}),
    ]
    if filename.endswith(".csv"):
        content = bank_csv(rows, fields)
    else:
        content = workbook([("Bank ledger", [fields, *([item.get(field, "") for field in fields] for item in rows)])])
        if filename.endswith(".zip"):
            content = archive([("bank.xls", content)])
    view = preview(client, case, content, filename)
    assert view["table_summary"]["profile"] == profile
    assert view["table_summary"]["reason_counts"] == {"transaction_valid": 1, "negative_amount": 1, "invalid_amount": 1}
    assert complete(client, case, view)["result"]["ok"] == 1
    base = f"/api/v1/cases/{case}"
    response = client.get(base + "/flows", params={"granularity": "observations"})
    assert response.status_code == 200, response.text
    observations = {item["trade_no"]: item for item in response.json()["items"]}
    assert set(observations) == {"SYN-TRANSACTION", "SYN-NEGATIVE"}
    flow = observations["SYN-TRANSACTION"]
    assert (flow["raw_payer_account"], flow["raw_payee_account"], flow["amount"], flow["balance"], flow["currency"]) == (
        "SYN-A", "SYN-B", "2.50", "-20.00", "USD")
    negative = observations["SYN-NEGATIVE"]["source_ledger"]
    assert (negative["signed_amount"], negative["economic_effect"]) == ("-1.25", "unknown")
    assert client.get(base + "/stats").json()["amount_totals"] == [{"currency": "USD", "amount": "2.50"}]


@pytest.mark.parametrize("patch", [
    """json_object('unrecognized_raw_record', 'synthetic')""",
    """json_object('source_sign_declaration', json_extract_string(details_json, '$.source_sign_declaration') || repeat(' ',2048))""",
])
def test_signed_package_rejects_unknown_or_oversized_metadata(client, database, make_case, tmp_path, patch):
    case = make_case("Synthetic signed package boundary")
    view = preview(client, case, raw([row("−12.30", STATEMENT)]))
    assert complete(client, case, view)["result"]["ok"] == 1
    package = client.get(f"/api/cases/{case}/export")
    assert package.status_code == 200, package.text
    contents = _members(package.content)
    _mutate(contents, "import_records", f"update entries set details_json=json_merge_patch(details_json,{patch})", tmp_path)
    before = database.q_raw("select id from cases order by id")
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", _archive(contents))})
    assert restored.status_code == 400, restored.text
    assert "自动表格语义元数据无效" in restored.json()["detail"]
    assert database.q_raw("select id from cases order by id") == before
