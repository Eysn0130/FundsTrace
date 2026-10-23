"""Public imports preserve timestamp identity and require direction evidence."""
from datetime import datetime
from decimal import Decimal
import hashlib
import io
import time

import pandas as pd
import pytest

from app import importer, table_import
from app.services import imports


def upload(client, case_id, content, filename="synthetic.csv"):
    response = client.post(f"/api/v1/cases/{case_id}/imports/preview",
                           files={"file": (filename, content.encode() if isinstance(content, str) else content)})
    assert response.status_code == 200, response.text
    return response.json()


def complete(client, case_id, preview):
    base = f"/api/v1/cases/{case_id}/imports/{preview['import_id']}"
    response = client.post(base + "/commit", json={
        "mode": preview["mode"], "mapping": preview["mapping"], "mark_accounts": [],
    })
    assert response.status_code == 200, response.text
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(base)
        assert response.status_code == 200, response.text
        job = response.json()
        if job["stage"] in ("done", "error"):
            assert job["stage"] == "done", job
            return job["result"]
        time.sleep(.01)
    raise AssertionError("Synthetic import timed out")


def flows(client, case_id):
    response = client.get(f"/api/v1/cases/{case_id}/flows")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def sources(client, case_id, flow_id):
    response = client.get(f"/api/v1/cases/{case_id}/flows/{flow_id}/sources")
    assert response.status_code == 200, response.text
    return response.json()["items"]


@pytest.fixture(params=["legacy-v4", "current"])
def source_profile(request):
    return request.param


def upload_for_profile(client, case_id, content, source_profile, monkeypatch, filename="synthetic.csv"):
    if source_profile == "current":
        return upload(client, case_id, content, filename)
    # Recreate an existing v4 ready session. Restore the new upload default
    # before commit, so the ready session must retain its historical contract.
    with monkeypatch.context() as historical:
        historical.setattr(imports, "TABLE_PROFILE", importer.PARSER_VERSION)
        preview = upload(client, case_id, content, filename)
    assert preview.get("table_summary") is None
    return preview


def journal(client, case_id, preview):
    response = client.get(f"/api/v1/cases/{case_id}/imports/{preview['import_id']}/records")
    assert response.status_code == 200, response.text
    return response.json()["items"]


@pytest.mark.parametrize("trade_no", ["", "SYN-SHARED"])
def test_http_subsecond_identity_exact_money_reimport_and_case_isolation(
        client, database, make_case, trade_no, source_profile, monkeypatch):
    case_id, other_id = make_case("Subsecond identity"), make_case("Other subsecond case")
    content = "交易流水号,交易时间,付款方账号,收款方账号,金额,余额\n" + "".join(
        f"{trade_no},2026-01-01 10:00:00.{fraction},SYN-A,SYN-B,900719925474099.91,9999999999999999.99\n"
        for fraction in ("100000", "200000", "200001", "100000"))
    first = upload_for_profile(client, case_id, content, source_profile, monkeypatch)
    assert first["stats"]["ok"] == 4
    result = complete(client, case_id, first)
    assert (result["ok"], result["rows_dup"], result["skipped"]) == (3, 1, 0)
    original = flows(client, case_id)
    assert {datetime.fromisoformat(row["trade_time"]).microsecond for row in original} == {100000, 200000, 200001}
    assert {row["amount"] for row in original} == {"900719925474099.91"}
    assert {row["balance"] for row in original} == {"9999999999999999.99"}
    assert database.con().execute("select sum(amount) from flows where case_id=?", [case_id]).fetchone()[0] == Decimal("2702159776422299.73")
    expected_rows = {100000: [2, 5], 200000: [3], 200001: [4]}
    for row in original:
        records = sources(client, case_id, row["id"])
        fraction = datetime.fromisoformat(row["trade_time"]).microsecond
        physical = expected_rows[fraction]
        expected_numbers = physical if source_profile == "legacy-v4" else [position - 1 for position in physical]
        assert sorted(record["source_row"] for record in records) == expected_numbers
        if source_profile == "current":
            assert sorted(record["member_row"] for record in records) == physical
            assert {record["member_path"] for record in records} == {"table.csv"}
            assert {record["row_kind"] for record in records} == {"table_record"}
        else:
            assert {record["row_kind"] for record in records} == {"csv_record"}
        assert {record["source_id"] for record in records} == {first["import_id"]}
        assert sorted(record["disposition"] for record in records) == (["duplicate", "imported"] if fraction == 100000 else ["imported"])

    repeated = upload_for_profile(client, case_id, content, source_profile, monkeypatch)
    result = complete(client, case_id, repeated)
    assert (result["ok"], result["rows_dup"]) == (0, 4)
    assert {row["id"] for row in flows(client, case_id)} == {row["id"] for row in original}
    for row in original:
        records = [record for record in sources(client, case_id, row["id"])
                   if record["source_id"] == repeated["import_id"]]
        physical = expected_rows[datetime.fromisoformat(row["trade_time"]).microsecond]
        expected_numbers = physical if source_profile == "legacy-v4" else [position - 1 for position in physical]
        assert sorted(record["source_row"] for record in records) == expected_numbers
        if source_profile == "current":
            assert sorted(record["member_row"] for record in records) == physical
        assert all(record["disposition"] == "duplicate" for record in records)

    other = upload_for_profile(client, other_id, content, source_profile, monkeypatch)
    result = complete(client, other_id, other)
    assert (result["ok"], result["rows_dup"]) == (3, 1)
    assert {row["id"] for row in flows(client, other_id)}.isdisjoint(row["id"] for row in original)
    assert client.get(f"/api/v1/cases/{other_id}/flows/{original[0]['id']}/sources").status_code == 404


def test_http_existing_timestamp_rows_and_source_versions_remain_compatible(
        client, make_case, monkeypatch, source_profile):
    case_id = make_case("Previous parser provenance")
    header = "交易时间,付款方账号,收款方账号,金额\n"
    one = "2026-01-01 10:00:00.100000,SYN-A,SYN-B,0.01\n"
    two = "2026-01-01 10:00:00.200000,SYN-A,SYN-B,0.01\n"
    with monkeypatch.context() as previous_parser:
        previous_parser.setattr(importer, "PARSER_VERSION", "funds-trace-table-v3")
        previous_parser.setattr(imports, "TABLE_PROFILE", "funds-trace-table-v3")
        previous = upload(client, case_id, header + one)
        assert complete(client, case_id, previous)["ok"] == 1
    original = flows(client, case_id)[0]
    current = upload_for_profile(client, case_id, header + one + two, source_profile, monkeypatch)
    result = complete(client, case_id, current)
    assert (result["ok"], result["rows_dup"]) == (1, 1)
    records = sources(client, case_id, original["id"])
    assert {record["source_id"]: record["parser_version"] for record in records} == {
        previous["import_id"]: "funds-trace-table-v3",
        current["import_id"]: "funds-trace-table-v4" if source_profile == "legacy-v4" else table_import.PROFILE,
    }
    assert {record["source_id"]: record["disposition"] for record in records} == {
        previous["import_id"]: "imported", current["import_id"]: "duplicate",
    }
    assert len(flows(client, case_id)) == 2


@pytest.mark.parametrize("direction", ["", "未知"])
def test_http_mode_b_unresolved_direction_is_skipped_with_source_record(
        client, make_case, direction, source_profile, monkeypatch):
    case_id, other_id = make_case("Unresolved direction"), make_case("Other direction case")
    content = ("Synthetic title\n\n交易时间,本方账号,对方账号,收支标志,金额\n"
               f"2026-01-01 10:00:00,SYN-SELF,SYN-UNKNOWN,{direction},25.00\n"
               "2026-01-01 11:00:00,SYN-SELF,SYN-IN,收入,50.01\n"
               "2026-01-01 12:00:00,SYN-SELF,SYN-OUT,支出,+0.01\n")
    preview = upload_for_profile(client, case_id, content, source_profile, monkeypatch)
    assert preview["mode"] == "B"
    skipped = 1 if source_profile == "legacy-v4" else 3
    assert (preview["stats"]["ok"], preview["stats"]["skipped"]) == (2, skipped)
    if source_profile == "legacy-v4":
        assert preview["stats"]["bad_examples"] == [{
            "row": "2026-01-01 10:00:00", "reason": "收支方向无法确定", "source_row": 4,
        }]
    else:
        # The complete journal also accounts for the title and blank record.
        summary = preview["table_summary"]
        assert (summary["record_total"], summary["unresolved"], summary["non_transaction"], summary["auxiliary"]) == (5, 1, 1, 1)
        assert summary["currency_totals"] == {"CNY": "50.02"}
        reason = "missing_direction" if not direction else "invalid_direction"
        assert summary["reason_counts"] == {"preamble": 1, "blank_record": 1, reason: 1, "transaction_valid": 2}
    result = complete(client, case_id, preview)
    assert (result["ok"], result["rows_dup"], result["skipped"]) == (2, 0, skipped)
    assert result["bad_examples"] == preview["stats"]["bad_examples"]
    if source_profile == "current":
        records = journal(client, case_id, preview)
        assert [record["record_no"] for record in records] == [1, 2, 3, 4, 5]
        assert [record["source_row"] for record in records] == [1, 2, 4, 5, 6]
        assert [record["disposition"] for record in records] == ["auxiliary", "non_transaction", "unresolved", "imported", "imported"]
        assert records[2]["reason_code"] == reason and records[2]["flow_id"] is None
        assert preview["stats"]["bad_examples"] == [{
            "row": "来源记录", "reason": records[2]["details"]["reason"], "source_row": 4,
        }]
    original = flows(client, case_id)
    assert {(row["payer_account"], row["payee_account"], row["amount"]) for row in original} == {
        ("SYN-IN", "SYN-SELF", "50.01"), ("SYN-SELF", "SYN-OUT", "0.01"),
    }
    records = [record for row in original for record in sources(client, case_id, row["id"])]
    assert sorted(record["source_row"] for record in records) == ([5, 6] if source_profile == "legacy-v4" else [4, 5])
    if source_profile == "current":
        assert sorted(record["member_row"] for record in records) == [5, 6]
    result = complete(client, case_id, upload_for_profile(client, case_id, content, source_profile, monkeypatch))
    assert (result["ok"], result["rows_dup"], result["skipped"]) == (0, 2, skipped)
    assert len(flows(client, case_id)) == 2
    result = complete(client, other_id, upload_for_profile(client, other_id, content, source_profile, monkeypatch))
    assert (result["ok"], result["rows_dup"], result["skipped"]) == (2, 0, skipped)
    assert {row["id"] for row in flows(client, other_id)}.isdisjoint(row["id"] for row in original)


def test_http_mode_b_keeps_direction_amount_column_and_signed_amount_contract(
        client, make_case, source_profile, monkeypatch):
    case_id = make_case("Direction evidence and signed amounts")
    content = ("交易时间,本方账号,对方账号,收支标志,金额,收入金额,支出金额\n"
               "2026-01-01 10:00:00,SYN-SELF,SYN-EXPLICIT,收入,+1.01,,9.99\n"
               "2026-01-01 10:01:00,SYN-SELF,SYN-INC,,,+2.02,\n"
               "2026-01-01 10:02:00,SYN-SELF,SYN-EXP,未知,,,3.03\n"
               "2026-01-01 10:03:00,SYN-SELF,SYN-AMOUNT,,4.04,,8.08\n"
               "2026-01-01 10:04:00,SYN-SELF,SYN-NEGATIVE,支出,-5.05,,\n"
               "2026-01-01 10:05:00,SYN-SELF,SYN-BRACKETS,收入,(6.06),,\n")
    preview = upload_for_profile(client, case_id, content, source_profile, monkeypatch)
    if source_profile == "legacy-v4":
        assert (preview["stats"]["ok"], preview["stats"]["skipped"]) == (4, 2)
        assert [(item["source_row"], item["reason"]) for item in preview["stats"]["bad_examples"]] == [(6, "金额无效"), (7, "金额无效")]
    else:
        assert (preview["stats"]["ok"], preview["stats"]["skipped"]) == (1, 5)
        assert preview["table_summary"]["currency_totals"] == {"CNY": "2.02"}
        assert preview["table_summary"]["reason_counts"] == {
            "conflicting_direction": 1, "transaction_valid": 1, "invalid_direction": 1,
            "conflicting_amount": 1, "negative_amount": 2,
        }
        assert [item["source_row"] for item in preview["stats"]["bad_examples"]] == [2, 4, 5]
    result = complete(client, case_id, preview)
    if source_profile == "legacy-v4":
        assert (result["ok"], result["skipped"]) == (4, 2)
        expected = {("SYN-EXPLICIT", "SYN-SELF", "1.01"), ("SYN-INC", "SYN-SELF", "2.02"),
                    ("SYN-SELF", "SYN-EXP", "3.03"), ("SYN-SELF", "SYN-AMOUNT", "4.04")}
    else:
        assert (result["ok"], result["skipped"]) == (1, 5)
        expected = {("SYN-INC", "SYN-SELF", "2.02")}
        records = journal(client, case_id, preview)
        assert [record["record_no"] for record in records] == list(range(1, 7))
        assert [record["source_row"] for record in records] == list(range(2, 8))
        assert [record["reason_code"] for record in records] == [
            "conflicting_direction", "transaction_valid", "invalid_direction", "conflicting_amount", "negative_amount", "negative_amount",
        ]
        assert [record["disposition"] for record in records] == ["unresolved", "imported", "unresolved", "unresolved", "unresolved", "unresolved"]
        assert set(records[0]["details"]["validation_issues"].split(",")) == {"conflicting_direction", "conflicting_amount"}
        assert all(record["flow_id"] is None for record in records if record["disposition"] == "unresolved")
    assert {(row["payer_account"], row["payee_account"], row["amount"]) for row in flows(client, case_id)} == expected


@pytest.mark.parametrize("filename, expected_filename, excel", [
    ("synthetic", "synthetic", False),
    ("s" * 241 + ".csv", "s" * 236 + ".csv", False),
    ("s" * 241 + ".XLSX", "s" * 235 + ".XLSX", True),
    ("../folder/synthetic.csv", "synthetic.csv", False),
    (r"C:\fakepath\synthetic.csv", "synthetic.csv", False),
], ids=["extensionless-csv", "long-csv", "long-excel", "posix-basename", "windows-basename"])
def test_http_filename_normalization_preserves_format_and_source(
        client, make_case, filename, expected_filename, excel, source_profile, monkeypatch):
    case_id = make_case("Filename format and provenance")
    content = ("交易时间,付款方账号,收款方账号,金额\n"
               "2026-01-01 10:00:00,SYN-A,SYN-B,0.01\n").encode()
    if excel:
        stream = io.BytesIO()
        pd.DataFrame({"交易时间": ["2026-01-01 10:00:00"], "付款方账号": ["SYN-A"],
                      "收款方账号": ["SYN-B"], "金额": ["0.01"]}).to_excel(
                          stream, index=False, sheet_name="Synthetic ledger")
        content = stream.getvalue()
    preview = upload_for_profile(client, case_id, content, source_profile, monkeypatch, filename)
    assert preview["filename"] == expected_filename
    assert preview["stats"]["ok"] == 1
    result = complete(client, case_id, preview)
    assert (result["ok"], result["rows_dup"], result["skipped"]) == (1, 0, 0)
    imported = flows(client, case_id)
    assert len(imported) == 1 and imported[0]["amount"] == "0.01"
    records = sources(client, case_id, imported[0]["id"])
    assert len(records) == 1
    record = records[0]
    assert record["filename"] == expected_filename
    assert record["source_id"] == preview["import_id"]
    assert record["sha256"] == hashlib.sha256(content).hexdigest()
    assert record["size_bytes"] == len(content)
    if source_profile == "legacy-v4":
        assert (record["row_kind"], record["sheet"], record["source_row"]) == (
            ("worksheet_row", "Synthetic ledger", 2) if excel else ("csv_record", "", 2))
    else:
        assert (record["row_kind"], record["sheet"], record["source_row"], record["member_row"]) == (
            "table_record", "workbook" if excel else "", 1, 2)
        assert record["record_details"]["sheet"] == ("Synthetic ledger" if excel else "")
        assert record["member_path"] == preview["table_summary"]["members"][0]["member_path"]
    original = client.get(f"/api/v1/cases/{case_id}/sources/{preview['import_id']}/file")
    assert original.status_code == 200 and original.content == content


@pytest.mark.parametrize("filename", ["synthetic.exe", "s" * 241 + ".exe", "s" * 241 + ".csv.exe"],
                         ids=["unsupported", "long-unsupported", "long-double-suffix"])
def test_http_unsupported_real_suffix_is_rejected_before_upload_storage(client, database, make_case, filename):
    case_id = make_case("Unsupported real suffix")
    content = ("交易时间,付款方账号,收款方账号,金额\n"
               "2026-01-01 10:00:00,SYN-A,SYN-B,0.01\n").encode()
    response = client.post(f"/api/v1/cases/{case_id}/imports/preview", files={"file": (filename, content)})
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "unsupported_file_type"
    assert client.get(f"/api/v1/cases/{case_id}/imports").json()["items"] == []
    assert flows(client, case_id) == []
    directory = database.DATA_DIR / "uploads"
    assert not directory.exists() or list(directory.iterdir()) == []
