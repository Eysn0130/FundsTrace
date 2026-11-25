"""Public import recheck starts with the declared scope and original code."""
import csv
import io
import json

from openpyxl import Workbook

from app import case_packages, import_preview_cache, table_import
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_feedback_package import _archive, _members, _mutate


BASE = ["交易时间", "本方账号", "对方账号", "交易金额(分)", "收付标志", "币种"]
DECLARED = "收付标志（X1=收入；X2=支出）"
OPPOSITE = "收付标志（X1=支出；X2=收入）"
HEADER = [*BASE[:4], DECLARED, BASE[5]]
DETAIL = "source_direction_recheck"


def row(code, amount, *, hour=10, currency="CNY"):
    return [f"2026-01-01 {hour:02}:00:00", "000SYN-A", "000SYN-B", amount, code, currency]


def csv_bytes(*rows):
    sink = io.StringIO(newline="")
    csv.writer(sink).writerows(rows)
    return sink.getvalue().encode()


def test_public_csv_recheck_replays_exact_same_region_and_preserves_direct_and_old(client, database, make_case):
    cid = make_case("Synthetic direction recheck")
    content = csv_bytes(BASE, row("出", "401"), HEADER, row("X1", "102", hour=11),
                        row("X2", "303", hour=12), row("x1", "404", hour=13),
                        BASE, row("X2", "505", hour=14))
    old = table_import.parse(content, suffix=".csv", profile=table_import.VOUCHER_REFERENCE_PROFILE)
    assert [record["source_row"] for record in old["records"] if record["disposition"] == "accepted"] == [2]

    view = preview(client, cid, content)
    summary = view["table_summary"]
    assert summary["profile"] == "funds-trace-table-v87"
    assert (summary["accepted"], summary["unresolved"]) == (3, 2)
    assert summary["currency_totals"] == {"CNY": "8.06"}
    assert summary["documents"][0]["direction_recheck"] == {"verified": 2, "pending": 1}
    assert [(entry["source_row"], entry["reason_code"]) for entry in summary["unresolved_examples"]] == [
        (6, "invalid_direction"), (8, "invalid_direction")]
    assert DETAIL not in summary["unresolved_examples"][1]["details"]
    unresolved = json.loads(summary["unresolved_examples"][0]["details"][DETAIL])
    assert (unresolved["status"], unresolved["header_row"]) == ("undeclared_code", 3)
    assert unresolved["columns"] == [{"column": DECLARED, "column_index": 5, "raw": "x1"}]

    import_id = view["import_id"]
    base = f"/api/v1/cases/{cid}/imports/{import_id}"
    import_preview_cache.discard()
    refreshed = client.post(base + "/preview", json={"mode": view["mode"], "mapping": view["mapping"]})
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["table_summary"] == summary
    import_preview_cache.discard()
    job = complete(client, cid, refreshed.json())
    assert job["stage"] == "done", job
    assert job["result"]["table_summary"]["currency_totals"] == {"CNY": "8.06"}
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    expected = {
        ("000SYN-A", "000SYN-B", "4.01"),
        ("000SYN-B", "000SYN-A", "1.02"),
        ("000SYN-A", "000SYN-B", "3.03"),
    }
    assert {(flow["payer_account"], flow["payee_account"], flow["amount"]) for flow in flows} == expected

    journal = client.get(base + "/records").json()["items"]
    source_flow = next(record["flow_id"] for record in journal if record["source_row"] == 4)
    sources = client.get(f"/api/v1/cases/{cid}/flows/{source_flow}/sources")
    assert sources.status_code == 200, sources.text
    linked_ledger = json.loads(sources.json()["items"][0]["record_details"]["source_ledger_evidence"])
    assert (linked_ledger["direction_heading"], linked_ledger["source_direction"], linked_ledger["direction"]) == (
        DECLARED, "X1", "in")
    accepted = {record["source_row"]: record["details"] for record in journal if record["disposition"] == "imported"}
    assert DETAIL not in accepted[2]
    for position, direction, code in ((4, "in", "X1"), (5, "out", "X2")):
        proof = json.loads(accepted[position][DETAIL])
        assert (proof["status"], proof["header_row"], proof["direction"]) == ("verified", 3, direction)
        assert proof["columns"] == [{"column": DECLARED, "column_index": 5, "raw": code}]
        assert any(item["column"] == DECLARED and item["raw"] == code
                   for item in json.loads(accepted[position]["source_field_evidence"]))
        ledger = json.loads(accepted[position]["source_ledger_evidence"])
        assert (ledger["header_scope"], ledger["direction_heading"], ledger["source_direction"], ledger["direction"]) == (
            3, DECLARED, code, direction)
    assert client.get(f"/api/v1/cases/{cid}/sources/{import_id}/file").content == content
    report = client.get(base + "/report", params={"member_path": "table.csv"})
    assert report.status_code == 200, report.text
    assert (report.json()["summary"]["in_amount"], report.json()["summary"]["out_amount"]) == ("1.02", "7.04")
    coverage = client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]
    assert coverage["unresolved_records"] == 2 and coverage["financial_interpretation_complete"] is False
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    restored_flows = client.get(f"/api/v1/cases/{restored}/flows").json()["items"]
    assert {(flow["payer_account"], flow["payee_account"], flow["amount"]) for flow in restored_flows} == expected
    assert database.q_raw("select parser_version from import_sources where case_id=?", [restored]) == [
        {"parser_version": "funds-trace-table-v87"}]


def test_public_xlsx_recheck_rejects_competing_columns_wrong_scope_and_nonliteral_values(client, make_case):
    cid = make_case("Synthetic direction recheck workbook")
    workbook = Workbook()
    first = workbook.active
    first.append(HEADER)
    first.append(row("X1", "301", currency="USD"))
    first.append(row('="X1"', "302"))
    first.append(row("X1", "303"))
    first["E4"].data_type = "e"  # Even a producer's error cell with a matching cached token has no authority.
    first.append(row("X1", "broken"))
    first.append(row("X" * 2048, "306"))
    conflict = workbook.create_sheet("Synthetic conflicting columns")
    conflict.append([*HEADER, OPPOSITE])
    conflict.append([*row("X1", "401"), "X1"])
    conflict.append([*HEADER, "收付标志"])
    conflict.append([*row("X1", "402"), "出"])
    conflict.append([*row("X1", "403"), ""])
    conflict.append([*row("X1", "404"), "入"])
    conflict.append([*row("", "405"), "出"])
    foreign = workbook.create_sheet("Synthetic independent region")
    foreign.append(BASE)
    foreign.append(row("X1", "501"))
    sink = io.BytesIO()
    workbook.save(sink)
    workbook.close()

    content = sink.getvalue()
    old = table_import.parse(content, suffix=".xlsx", profile=table_import.VOUCHER_REFERENCE_PROFILE)
    assert {record["source_row"] for record in old["records"]
            if record["disposition"] == "accepted" and record["member_path"] == "worksheets/0002"} == {4, 6, 7}
    view = preview(client, cid, content, "synthetic.xlsx")
    summary = view["table_summary"]
    assert summary["profile"] == "funds-trace-table-v87"
    assert summary["currency_totals"] == {"CNY": "8.09", "USD": "3.01"}
    assert (summary["accepted"], summary["unresolved"]) == (3, 8)
    assert summary["reason_counts"]["conflicting_direction"] == 3
    by_row = {(example["member_path"], example["source_row"]): example for example in summary["unresolved_examples"]}
    for pos in (3, 4):
        example = by_row["worksheets/0001", pos]
        assert json.loads(example["details"][DETAIL])["status"] == "non_literal_code"
    invalid_amount = by_row["worksheets/0001", 5]
    assert invalid_amount["reason_code"] == "invalid_amount"
    assert json.loads(invalid_amount["details"][DETAIL])["status"] == "validation_failed"
    bounded = json.loads(by_row["worksheets/0001", 6]["details"][DETAIL])
    assert bounded["columns"][0]["raw"] is None
    for pos in (2, 4, 5):
        example = by_row["worksheets/0002", pos]
        proof = json.loads(example["details"][DETAIL])
        assert proof["status"] == "conflicting_declaration"
        assert len(proof["columns"]) == 2
    assert json.loads(by_row["worksheets/0002", 4]["details"][DETAIL])["columns"] == [
        {"column": DECLARED, "column_index": 5, "raw": "X1"},
        {"column": "收付标志", "column_index": 7, "raw": "出"},
    ]
    assert DETAIL not in by_row["worksheets/0003", 2]["details"]
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 3, job
    base = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    journal = client.get(base + "/records").json()["items"]
    conflicted = next(record for record in journal
                      if record["member_path"] == "worksheets/0002" and record["source_row"] == 4)
    assert (conflicted["disposition"], conflicted["flow_id"]) == ("unresolved", None)
    assert "source_ledger_evidence" not in conflicted["details"]
    assert all(DETAIL not in record["details"] for record in journal
               if record["member_path"] == "worksheets/0002" and record["disposition"] == "imported")


def test_matching_code_opposite_to_existing_split_direction_stays_pending(client, make_case):
    cid = make_case("Synthetic direction conflict with income column")
    header = [*HEADER, "收入金额(分)", "支出金额(分)"]
    content = csv_bytes(header, [*row("X2", "101"), "101", "0"])
    legacy = table_import.parse(content, suffix=".csv", profile=table_import.VOUCHER_REFERENCE_PROFILE)
    assert legacy["stats"]["currency_totals"] == {"CNY": "1.01"}
    view = preview(client, cid, content)
    assert view["table_summary"]["accepted"] == 0
    assert view["table_summary"]["reason_counts"] == {"conflicting_direction": 1}


def test_frozen_ready_profile_and_package_proof_are_checked_at_the_public_entry(
        client, database, make_case, monkeypatch, tmp_path):
    cid = make_case("Synthetic versioned direction evidence")
    content = csv_bytes(HEADER, row("X1", "101"))
    with monkeypatch.context() as frozen:
        # Create a ready session exactly as the predecessor did for this input.
        frozen.delitem(table_import.CLOSED_BANK_SUCCESSORS, table_import.VOUCHER_REFERENCE_PROFILE)
        old = preview(client, cid, content)
    assert old["table_summary"]["profile"] == "funds-trace-table-v69"
    import_preview_cache.discard()
    url = f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview"
    rerun = client.post(url, json={"mode": old["mode"], "mapping": old["mapping"]})
    assert rerun.status_code == 200, rerun.text
    assert rerun.json()["table_summary"]["accepted"] == 0
    assert rerun.json()["table_summary"]["profile"] == "funds-trace-table-v69"
    assert complete(client, cid, rerun.json())["result"]["ok"] == 0

    view = preview(client, cid, content)
    assert complete(client, cid, view)["result"]["ok"] == 1
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]["financial_interpretation_complete"] is True
    frozen_job = client.get(f"/api/v1/cases/{cid}/imports/{old['import_id']}").json()
    assert frozen_job["result"]["table_summary"]["unresolved"] == 1
    base = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    record = client.get(base + "/records").json()["items"][0]
    proof = json.loads(record["details"][DETAIL])
    proof["columns"][0]["column"] = "收付标志（X1=收入；X3=支出）"
    members = _members(case_packages.export_package(cid)[0])
    patch = json.dumps({DETAIL: json.dumps(proof, ensure_ascii=False)}, ensure_ascii=False).replace("'", "''")
    _mutate(members, "import_records", f"update entries set details_json=json_merge_patch(details_json,'{patch}') "
            f"where source_id='{view['import_id']}' and record_no=1", tmp_path)
    case_count = database.q_raw("select count(*) n from cases")[0]["n"]
    response = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", _archive(members))})
    assert response.status_code == 400, response.text
    assert database.q_raw("select count(*) n from cases")[0]["n"] == case_count
