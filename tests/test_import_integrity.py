"""Public imports preserve timestamp identity and require direction evidence."""
from datetime import datetime
from decimal import Decimal
import hashlib
import io
import time

import pandas as pd
import pytest

from app import importer


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


@pytest.mark.parametrize("trade_no", ["", "SYN-SHARED"])
def test_http_subsecond_identity_exact_money_reimport_and_case_isolation(client, database, make_case, trade_no):
    case_id, other_id = make_case("Subsecond identity"), make_case("Other subsecond case")
    content = "交易流水号,交易时间,付款方账号,收款方账号,金额,余额\n" + "".join(
        f"{trade_no},2026-01-01 10:00:00.{fraction},SYN-A,SYN-B,900719925474099.91,9999999999999999.99\n"
        for fraction in ("100000", "200000", "200001", "100000"))
    first = upload(client, case_id, content)
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
        assert sorted(record["source_row"] for record in records) == expected_rows[fraction]
        assert {record["source_id"] for record in records} == {first["import_id"]}
        assert sorted(record["disposition"] for record in records) == (["duplicate", "imported"] if fraction == 100000 else ["imported"])

    repeated = upload(client, case_id, content)
    result = complete(client, case_id, repeated)
    assert (result["ok"], result["rows_dup"]) == (0, 4)
    assert {row["id"] for row in flows(client, case_id)} == {row["id"] for row in original}
    for row in original:
        records = [record for record in sources(client, case_id, row["id"])
                   if record["source_id"] == repeated["import_id"]]
        assert sorted(record["source_row"] for record in records) == expected_rows[datetime.fromisoformat(row["trade_time"]).microsecond]
        assert all(record["disposition"] == "duplicate" for record in records)

    other = upload(client, other_id, content)
    result = complete(client, other_id, other)
    assert (result["ok"], result["rows_dup"]) == (3, 1)
    assert {row["id"] for row in flows(client, other_id)}.isdisjoint(row["id"] for row in original)
    assert client.get(f"/api/v1/cases/{other_id}/flows/{original[0]['id']}/sources").status_code == 404


def test_http_existing_timestamp_rows_and_source_versions_remain_compatible(client, make_case, monkeypatch):
    case_id = make_case("Previous parser provenance")
    header = "交易时间,付款方账号,收款方账号,金额\n"
    one = "2026-01-01 10:00:00.100000,SYN-A,SYN-B,0.01\n"
    two = "2026-01-01 10:00:00.200000,SYN-A,SYN-B,0.01\n"
    with monkeypatch.context() as previous_parser:
        previous_parser.setattr(importer, "PARSER_VERSION", "funds-trace-table-v3")
        previous = upload(client, case_id, header + one)
        assert complete(client, case_id, previous)["ok"] == 1
    original = flows(client, case_id)[0]
    current = upload(client, case_id, header + one + two)
    result = complete(client, case_id, current)
    assert (result["ok"], result["rows_dup"]) == (1, 1)
    records = sources(client, case_id, original["id"])
    assert {record["source_id"]: record["parser_version"] for record in records} == {
        previous["import_id"]: "funds-trace-table-v3", current["import_id"]: "funds-trace-table-v4",
    }
    assert {record["source_id"]: record["disposition"] for record in records} == {
        previous["import_id"]: "imported", current["import_id"]: "duplicate",
    }
    assert len(flows(client, case_id)) == 2


@pytest.mark.parametrize("direction", ["", "未知"])
def test_http_mode_b_unresolved_direction_is_skipped_with_source_record(client, make_case, direction):
    case_id, other_id = make_case("Unresolved direction"), make_case("Other direction case")
    content = ("Synthetic title\n\n交易时间,本方账号,对方账号,收支标志,金额\n"
               f"2026-01-01 10:00:00,SYN-SELF,SYN-UNKNOWN,{direction},25.00\n"
               "2026-01-01 11:00:00,SYN-SELF,SYN-IN,收入,50.01\n"
               "2026-01-01 12:00:00,SYN-SELF,SYN-OUT,支出,+0.01\n")
    preview = upload(client, case_id, content)
    assert preview["mode"] == "B"
    assert (preview["stats"]["ok"], preview["stats"]["skipped"]) == (2, 1)
    assert preview["stats"]["bad_examples"] == [{
        "row": "2026-01-01 10:00:00", "reason": "收支方向无法确定", "source_row": 4,
    }]
    result = complete(client, case_id, preview)
    assert (result["ok"], result["rows_dup"], result["skipped"]) == (2, 0, 1)
    assert result["bad_examples"] == preview["stats"]["bad_examples"]
    original = flows(client, case_id)
    assert {(row["payer_account"], row["payee_account"], row["amount"]) for row in original} == {
        ("SYN-IN", "SYN-SELF", "50.01"), ("SYN-SELF", "SYN-OUT", "0.01"),
    }
    assert sorted(record["source_row"] for row in original for record in sources(client, case_id, row["id"])) == [5, 6]
    result = complete(client, case_id, upload(client, case_id, content))
    assert (result["ok"], result["rows_dup"], result["skipped"]) == (0, 2, 1)
    assert len(flows(client, case_id)) == 2
    result = complete(client, other_id, upload(client, other_id, content))
    assert (result["ok"], result["rows_dup"], result["skipped"]) == (2, 0, 1)
    assert {row["id"] for row in flows(client, other_id)}.isdisjoint(row["id"] for row in original)


def test_http_mode_b_keeps_direction_amount_column_and_signed_amount_contract(client, make_case):
    case_id = make_case("Direction evidence and signed amounts")
    content = ("交易时间,本方账号,对方账号,收支标志,金额,收入金额,支出金额\n"
               "2026-01-01 10:00:00,SYN-SELF,SYN-EXPLICIT,收入,+1.01,,9.99\n"
               "2026-01-01 10:01:00,SYN-SELF,SYN-INC,,,+2.02,\n"
               "2026-01-01 10:02:00,SYN-SELF,SYN-EXP,未知,,,3.03\n"
               "2026-01-01 10:03:00,SYN-SELF,SYN-AMOUNT,,4.04,,8.08\n"
               "2026-01-01 10:04:00,SYN-SELF,SYN-NEGATIVE,支出,-5.05,,\n"
               "2026-01-01 10:05:00,SYN-SELF,SYN-BRACKETS,收入,(6.06),,\n")
    preview = upload(client, case_id, content)
    assert (preview["stats"]["ok"], preview["stats"]["skipped"]) == (4, 2)
    assert [(item["source_row"], item["reason"]) for item in preview["stats"]["bad_examples"]] == [(6, "金额无效"), (7, "金额无效")]
    result = complete(client, case_id, preview)
    assert (result["ok"], result["skipped"]) == (4, 2)
    assert {(row["payer_account"], row["payee_account"], row["amount"]) for row in flows(client, case_id)} == {
        ("SYN-EXPLICIT", "SYN-SELF", "1.01"), ("SYN-INC", "SYN-SELF", "2.02"),
        ("SYN-SELF", "SYN-EXP", "3.03"), ("SYN-SELF", "SYN-AMOUNT", "4.04"),
    }


@pytest.mark.parametrize("filename, expected_filename, excel", [
    ("synthetic", "synthetic", False),
    ("s" * 241 + ".csv", "s" * 236 + ".csv", False),
    ("s" * 241 + ".XLSX", "s" * 235 + ".XLSX", True),
    ("../folder/synthetic.csv", "synthetic.csv", False),
    (r"C:\fakepath\synthetic.csv", "synthetic.csv", False),
], ids=["extensionless-csv", "long-csv", "long-excel", "posix-basename", "windows-basename"])
def test_http_filename_normalization_preserves_format_and_source(client, make_case, filename, expected_filename, excel):
    case_id = make_case("Filename format and provenance")
    content = ("交易时间,付款方账号,收款方账号,金额\n"
               "2026-01-01 10:00:00,SYN-A,SYN-B,0.01\n").encode()
    if excel:
        stream = io.BytesIO()
        pd.DataFrame({"交易时间": ["2026-01-01 10:00:00"], "付款方账号": ["SYN-A"],
                      "收款方账号": ["SYN-B"], "金额": ["0.01"]}).to_excel(
                          stream, index=False, sheet_name="Synthetic ledger")
        content = stream.getvalue()
    preview = upload(client, case_id, content, filename)
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
    assert (record["row_kind"], record["sheet"], record["source_row"]) == (
        ("worksheet_row", "Synthetic ledger", 2) if excel else ("csv_record", "", 2))
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
