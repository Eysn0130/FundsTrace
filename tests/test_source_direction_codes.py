"""Only a physical table region's complete declaration can give 0/1 meaning."""
import io
import json

from openpyxl import Workbook
import pytest

from app import event_resolution, import_preview_cache, table_import, table_sign_declarations
from app.services import imports
from test_automatic_table_import import preview
from test_feedback31_reference import COLUMNS, payload, row
from test_folder_evidence import archive
from test_import_lifecycle_api import complete
from xls_fixtures import workbook as biff_workbook


OUT_FIRST = "本表收付方向0为支出，1为收入"
IN_FIRST = "本表收付方向0为收入，1为支出"


def records(client, base, view):
    response = client.get(f"{base}/imports/{view['import_id']}/records")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def source_rows(codes, declarations):
    return [row(**{"交易时间": f"2026-01-0{index} 10:00:00", "交易流水号": f"SYN-CODE-{index}",
                   "收付标志": code, "交易是否成功": "01" if index == 2 else "1", "备注": declaration})
            for index, (code, declaration) in enumerate(zip(codes, declarations, strict=True), 1)]


def facts(client, base):
    response = client.get(base + "/flows", params={"granularity": "observations"})
    assert response.status_code == 200, response.text
    return {item["trade_no"]: (item["payer_account"], item["payee_account"], item["amount"])
            for item in response.json()["items"]
            if item["source_ledger"] is None}


@pytest.mark.parametrize("kind,profile", [
    ("csv", table_import.BANK_SOURCE_PROFILE),
    ("zip", table_import.BANK_SOURCE_ARCHIVE_PROFILE),
    ("xls", table_import.BANK_SOURCE_XLS_PROFILE),
])
@pytest.mark.parametrize("statement,zero_in", [(OUT_FIRST, False), (IN_FIRST, True)], ids=["zero-out", "zero-in"])
def test_public_declared_codes_use_exact_raw_values_and_keep_unknown_status(
        client, database, make_case, statement, zero_in, kind, profile):
    cid = make_case("Synthetic direction code table")
    base = f"/api/v1/cases/{cid}"
    rows = source_rows(("0", "1", "01"), ("", "", statement))
    if kind == "xls":
        raw = biff_workbook([("Synthetic coded movements", [COLUMNS, *[[item.get(key, "") for key in COLUMNS] for item in rows]])])
    else:
        raw = payload(rows)
        if kind == "zip":
            raw = archive([("synthetic-bank.csv", raw)])
    view = preview(client, cid, raw, f"codes.{kind}")
    assert view["table_summary"]["profile"] == profile
    evidence = next(item for item in view["table_summary"]["mapping_evidence"] if item["field"] == "direction")
    assert (evidence["column"], evidence["direction_code_row"], evidence["direction_code_column"]) == (
        "收付标志", "4", str(COLUMNS.index("备注") + 1))
    import_preview_cache.discard(view["import_id"])
    refreshed = client.post(f"{base}/imports/{view['import_id']}/preview",
                            json={"mode": view["mode"], "mapping": view["mapping"]})
    assert refreshed.status_code == 200, refreshed.text
    job = complete(client, cid, refreshed.json())
    assert job["result"]["ok"] == refreshed.json()["stats"]["ok"] == view["stats"]["ok"] == 2
    with database._lock:
        database._con.close()
        database._con = None
    assert client.get(f"{base}/imports/{view['import_id']}").json() == job
    expected = {f"SYN-CODE-{index}": (("SYN-B", "SYN-A", "12.34") if inward else ("SYN-A", "SYN-B", "12.34"))
                for index, inward in enumerate((zero_in, not zero_in), 1)}
    assert facts(client, base) == expected
    saved = records(client, base, view)
    assert [(item["source_row"], item["reason_code"], item["details"]["reported_status"]) for item in saved] == [
        (2, "transaction_valid", "unknown"), (3, "transaction_valid", "unknown"), (4, "invalid_direction", "unknown")]
    codebooks = [json.loads(item["details"]["source_ledger_evidence"]) for item in saved]
    for index, evidence in enumerate(codebooks[:2]):
        assert evidence["source_direction"] == str(index)
        proof = evidence[table_sign_declarations.DIRECTION_CODE_FIELD]
        assert table_sign_declarations.validate_direction_codes(proof)
        assert (proof["header_row"], proof["declaration_row"], proof["direction_column"], proof["statement"]) == (
            1, 4, COLUMNS.index("收付标志") + 1, statement)
    assert saved[-1]["disposition"] == "unresolved"
    assert (codebooks[-1]["source_direction"], codebooks[-1]["direction"]) == ("01", "unknown")
    assert client.get(f"{base}/sources/{view['import_id']}/file").content == raw

    package = client.get(f"/api/cases/{cid}/export")
    assert package.status_code == 200, package.text
    imported = client.post("/api/cases/import", files={"file": ("direction.fundcase", package.content)})
    assert imported.status_code == 200, imported.text
    restored = imported.json()["case_id"]
    assert facts(client, f"/api/v1/cases/{restored}") == expected
    restored_source, = database.q_raw("select source_id from import_sources where case_id=?", [restored])
    restored_records = records(client, f"/api/v1/cases/{restored}", {"import_id": restored_source["source_id"]})
    assert [item["details"] for item in restored_records] == [item["details"] for item in saved]


@pytest.mark.parametrize("declarations", [
    ("", ""),
    ("本表收付方向0为支出", ""),
    ("本文件收付方向0为支出，1为收入", ""),
    (OUT_FIRST, IN_FIRST),
    (OUT_FIRST, "本表 收付方向 0为收入，1为支出"),
], ids=["missing", "incomplete", "other-scope", "contradictory", "late-spaced-contradiction"])
def test_public_missing_or_incompatible_code_declarations_leave_records_unresolved(client, make_case, declarations):
    cid = make_case("Synthetic unproven direction code")
    base = f"/api/v1/cases/{cid}"
    view = preview(client, cid, payload(source_rows(("0", "1"), declarations)))
    job = complete(client, cid, view)
    assert view["table_summary"]["profile"] == table_import.BANK_SOURCE_PROFILE
    assert job["result"]["ok"] == view["stats"]["ok"] == 0
    assert facts(client, base) == {}
    assert all(item["disposition"] == "unresolved" and item["reason_code"] == "invalid_direction"
               for item in records(client, base, view))


@pytest.mark.parametrize("kind,old_profile,new_profile", [
    ("csv", "funds-trace-table-v69", "funds-trace-table-v84"),
    ("xls", "funds-trace-table-v70", "funds-trace-table-v85"),
    ("evidence", "funds-trace-table-v71", "funds-trace-table-v86"),
])
def test_public_ordinary_codebook_selects_new_profile_and_keeps_old_replay(
        client, make_case, monkeypatch, kind, old_profile, new_profile):
    headers = ("交易时间", "本方账号", "对方账号", "交易金额（分）", "收付方向", "备注", "交易币种", "交易流水号")
    statement = "本 表 收付方向 0为支出，1为收入"
    values = [
        ("2026-01-01 10:00:00", "SYN-A", "SYN-B", "1234", "0", "", "CNY", "SYN-CODE-0"),
        ("2026-01-02 10:00:00", "SYN-A", "SYN-B", "250", "1", statement, "USD", "SYN-CODE-1"),
    ]
    if kind == "xls":
        data = biff_workbook([("Synthetic ordinary", [headers, *values])])
    else:
        if kind == "evidence":
            headers += tuple(event_resolution.HEADERS.values())
            values = [(*item, "SYN-Issuer", "SYN-Channel", "SYN-Scope", item[-1]) for item in values]
        data = payload([dict(zip(headers, item, strict=True)) for item in values], headers)
    filename = "ordinary.xls" if kind == "xls" else "ordinary.csv"
    cid = make_case("Synthetic ordinary codebook")
    base = f"/api/v1/cases/{cid}"
    ready = preview(client, cid, data, filename)
    assert ready["table_summary"]["profile"] == new_profile
    assert ready["table_summary"]["currency_totals"] == {"CNY": "12.34", "USD": "2.50"}
    import_preview_cache.discard(ready["import_id"])
    assert complete(client, cid, ready)["result"]["ok"] == 2
    flows = client.get(base + "/flows", params={"granularity": "observations"}).json()["items"]
    assert {item["trade_no"]: (item["raw_payer_account"], item["raw_payee_account"], item["amount"], item["currency"])
            for item in flows} == {
        "SYN-CODE-0": ("SYN-A", "SYN-B", "12.34", "CNY"),
        "SYN-CODE-1": ("SYN-B", "SYN-A", "2.50", "USD"),
    }
    for item, raw_code in zip(records(client, base, ready), ("0", "1"), strict=True):
        ledger = json.loads(item["details"]["source_ledger_evidence"])
        assert ledger["source_direction"] == raw_code
        proof = ledger[table_sign_declarations.DIRECTION_CODE_FIELD]
        assert table_sign_declarations.validate_direction_codes(proof)
        assert (proof["statement"], proof["declaration_row"], proof["declaration_column"]) == (statement, 3, 6)
    assert client.get(f"{base}/sources/{ready['import_id']}/file").content == data

    legacy = make_case("Synthetic frozen ordinary codebook")
    parse = imports._parse
    with monkeypatch.context() as pinned:
        pinned.setattr(imports, "_parse", lambda *args, **options: parse(
            *args, **{**options, "table_profile": old_profile, "_auto_event_profile": False}))
        frozen = preview(client, legacy, data, filename)
    import_preview_cache.discard(frozen["import_id"])
    old_receipt = complete(client, legacy, frozen)["result"]
    assert old_receipt["table_summary"]["profile"] == old_profile
    assert old_receipt["ok"] == frozen["stats"]["ok"] == 0


def test_public_archive_without_registered_note_does_not_scan_transaction_values(client, make_case, monkeypatch):
    def unexpected_note_scan(value):
        pytest.fail(f"inspected a non-note cell for direction evidence: {value!r}")

    monkeypatch.setattr(table_sign_declarations, "mentions_direction_codes", unexpected_note_scan)
    headers = ("交易时间", "本方账号", "对方账号", "交易金额", "收付方向", "摘要")
    movement = dict(zip(headers, ("2026-01-01", "SYN-A", "SYN-B", "12.34", "出", OUT_FIRST), strict=True))
    data = archive([("movement.csv", payload([movement], headers))])
    cid = make_case("Synthetic archive without direction note")
    ready = preview(client, cid, data, "ordinary.zip")
    assert ready["table_summary"]["profile"] == "funds-trace-table-archive-v69"
    assert ready["table_summary"]["currency_totals"] == {"CNY": "12.34"}
    assert complete(client, cid, ready)["result"]["ok"] == 1


def test_public_code_declaration_cannot_cross_header_or_sheet(client, make_case):
    cid = make_case("Synthetic direction code scope")
    base = f"/api/v1/cases/{cid}"
    workbook = Workbook()
    entries = source_rows(("0", "1", "1"), (OUT_FIRST, "", ""))
    sheet = workbook.active
    for item in entries[:2]:
        sheet.append(COLUMNS)
        sheet.append([item.get(key, "") for key in COLUMNS])
    sheet = workbook.create_sheet("Synthetic second worksheet")
    sheet.append(COLUMNS)
    sheet.append([entries[2].get(key, "") for key in COLUMNS])
    data = io.BytesIO()
    workbook.save(data)
    workbook.close()
    view = preview(client, cid, data.getvalue(), "direction.xlsx")
    assert view["table_summary"]["profile"] == table_import.BANK_SOURCE_PROFILE
    job = complete(client, cid, view)
    assert job["result"]["ok"] == view["stats"]["ok"] == 1
    assert facts(client, base) == {"SYN-CODE-1": ("SYN-A", "SYN-B", "12.34")}
    assert [item["reason_code"] for item in records(client, base, view)] == [
        "transaction_valid", "repeated_header", "invalid_direction", "invalid_direction"]


def test_public_excel_zero_padding_cannot_be_used_as_a_direction_code(client, make_case):
    cid = make_case("Synthetic formatted direction cells")
    base = f"/api/v1/cases/{cid}"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(COLUMNS)
    for item in source_rows((1, 1), (OUT_FIRST, "")):
        sheet.append([item.get(key, "") for key in COLUMNS])
    sheet.cell(3, COLUMNS.index("收付标志") + 1).number_format = "00"
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    view = preview(client, cid, stream.getvalue(), "coded.xlsx")
    receipt = complete(client, cid, view)["result"]
    assert receipt["ok"] == view["stats"]["ok"] == 1
    assert facts(client, base) == {"SYN-CODE-1": ("SYN-B", "SYN-A", "12.34")}
    saved = records(client, base, view)
    assert [item["reason_code"] for item in saved] == ["transaction_valid", "invalid_direction"]
    assert json.loads(saved[1]["details"]["source_ledger_evidence"])["source_direction"] == "01"


def test_public_frozen_old_version_ignores_new_code_declaration(client, make_case, monkeypatch):
    cid = make_case("Synthetic historical direction interpretation")
    base = f"/api/v1/cases/{cid}"
    raw = payload(source_rows(("0", "1"), (OUT_FIRST, "")))
    parse = imports._parse

    def historical(*args, **options):
        return parse(*args, **{**options, "table_profile": table_import.BANK_EXPLICIT_CURRENCY_PROFILE,
                               "_auto_event_profile": False})

    with monkeypatch.context() as pinned:
        pinned.setattr(imports, "_parse", historical)
        view = preview(client, cid, raw)
    import_preview_cache.discard(view["import_id"])
    response = client.post(f"{base}/imports/{view['import_id']}/preview",
                           json={"mode": view["mode"], "mapping": view["mapping"]})
    assert response.status_code == 200, response.text
    after = response.json()
    result = complete(client, cid, after)["result"]
    assert result["table_summary"]["profile"] == table_import.BANK_EXPLICIT_CURRENCY_PROFILE
    assert result["ok"] == after["stats"]["ok"] == view["stats"]["ok"] == 0
    package = client.get(f"/api/cases/{cid}/export")
    assert package.status_code == 200, package.text
    restored = client.post("/api/cases/import", files={"file": ("old-direction.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    assert facts(client, f"/api/v1/cases/{restored.json()['case_id']}") == {}


@pytest.mark.parametrize("other_field,other_value,reason", [
    ("收支方向", "入", "invalid_direction"),
    ("收入金额", "12.34", "conflicting_direction"),
])
def test_public_explicit_direction_conflict_cannot_be_overridden_by_a_codebook(
        client, make_case, other_field, other_value, reason):
    cid = make_case("Synthetic competing direction evidence")
    base = f"/api/v1/cases/{cid}"
    movement, = source_rows(("0",), (OUT_FIRST,))
    movement[other_field] = other_value
    member = payload([movement], (*COLUMNS, other_field))
    view = preview(client, cid, archive([("synthetic-bank.csv", member)]), "direction.zip")
    assert view["table_summary"]["profile"] == table_import.BANK_SOURCE_ARCHIVE_PROFILE
    assert complete(client, cid, view)["result"]["ok"] == view["stats"]["ok"] == 0
    item, = records(client, base, view)
    assert (item["reason_code"], item["disposition"]) == (reason, "unresolved")
    assert facts(client, base) == {}
