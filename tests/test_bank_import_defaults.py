"""Synthetic bank amount rules exercised through the ordinary upload and commit."""
import csv
import io
import json

from openpyxl import Workbook
import pytest

from app import feedback_import, import_preview_cache
from app.services import imports
from test_automatic_table_import import preview
from test_feedback31_reference import COLUMNS as BANK31_COLUMNS, payload, row
from test_feedback_import_api import complete
from test_folder_evidence import archive


@pytest.mark.parametrize(
    "headers, filename, amount_column, source_amounts, expected_profile",
    [
        (feedback_import.COLUMNS, "bank28.csv", "交易金额", ("12.34", "2.50"), "funds-trace-table-v81"),
        (BANK31_COLUMNS, "bank31.csv", "交易金额", ("12.34", "2.50"), "funds-trace-table-v81"),
        (BANK31_COLUMNS, "bank31.xlsx", "交易金额（分）", ("1234", "250"), "funds-trace-table-v81"),
        (BANK31_COLUMNS, "bank31.zip", "交易金额（分）", ("1234", "250"), "funds-trace-table-archive-v69"),
    ],
)
def test_bank_amount_currency_defaults_and_explicit_fields_survive_commit(
    client, make_case, headers, filename, amount_column, source_amounts, expected_profile,
):
    # 12.34 base units or 1,234 cents are both 12.34. The second row
    # independently declares USD; 2.50 base units or 250 cents are 2.50 USD.
    first, second = source_amounts
    columns = [amount_column if name == "交易金额" else name for name in headers]
    rows = [
        row(**{"交易流水号": "SYN-DEFAULT", amount_column: first, "交易币种": ""}),
        row(**{"交易流水号": "SYN-USD", "交易时间": "2026-01-02 10:00:00",
               amount_column: second, "交易币种": "USD"}),
    ]
    original = payload(rows, columns, "xlsx" if filename.endswith(".xlsx") else "csv")
    if filename.endswith(".zip"):
        original = archive([("synthetic-bank.csv", original)])
    case = make_case("SYN bank amount defaults")
    view = preview(client, case, original, filename)
    assert view["table_summary"]["currency_totals"] == {"CNY": "12.34", "USD": "2.50"}
    assert view["stats"]["ok"] == 2
    job = complete(client, case, view)
    assert (job["stage"], job["result"]["ok"]) == ("done", 2)
    assert job["result"]["table_summary"]["profile"] == expected_profile

    base = f"/api/v1/cases/{case}"
    items = client.get(base + "/flows", params={"granularity": "observations"}).json()["items"]
    assert {item["trade_no"]: (item["currency"], item["amount"]) for item in items} == {
        "SYN-DEFAULT": ("CNY", "12.34"),
        "SYN-USD": ("USD", "2.50"),
    }
    source = base + "/sources/" + view["import_id"]
    records = client.get(source + "/report").json()["items"]
    assert [(item["source_row"], item["disposition"]) for item in records] == [(2, "imported"), (3, "imported")]
    for record, amount, currency in zip(records, source_amounts, ("", "USD")):
        evidence = {item["column"]: item["raw"]
                    for item in json.loads(record["details"]["source_field_evidence"])}
        assert evidence[amount_column] == amount
        assert evidence["交易币种"] == currency
    assert records[0]["details"]["currency_basis"] == "银行交易明细币种空白，按规则使用CNY"
    expected_basis = ("银行交易明细金额单位未声明，按元解释" if amount_column == "交易金额"
                      else "采用表头或本行声明的金额单位")
    assert [item["details"]["amount_unit_basis"] for item in records] == [expected_basis, expected_basis]
    assert client.get(source + "/file").content == original


@pytest.mark.parametrize("money_header,units,expected", [
    ("交易金额", ("", "分", "美元", "SYN-INVALID"), {"CNY": "11.22", "USD": "3.50"}),
    ("交易金额（USD）", ("", "", "", ""), {"USD": "17.22"}),
])
def test_declared_unit_currency_and_scope(client, make_case, money_header, units, expected):
    # 8.72 yuan + 250 cents gives 11.22 CNY. 3.50 explicit USD stays separate.
    # In the annotated header case, 8.72 + 2.50 + 3.50 + 2.50 is 17.22 USD.
    # An unrecognized nonempty unit rejects a row, as does conflicting currency.
    headers = tuple(money_header if h == "交易金额" else h for h in BANK31_COLUMNS) + ("金额单位",)
    source_amounts = ("8.72", "250", "3.50", "2.50") if money_header == "交易金额" else (
        "8.72", "2.50", "3.50", "2.50")
    rows = [row(**{"交易流水号": f"SYN-UNIT-{index}", money_header: amount, "金额单位": unit, "交易币种": ""})
            for index, (amount, unit) in enumerate(zip(source_amounts, units), 1)]
    rows.append(row(**{"交易流水号": "SYN-AT-ODDS", money_header: "2.50", "金额单位": "人民币元", "交易币种": "JPY"}))
    case = make_case("SYN bank declared unit currency")
    view = preview(client, case, payload(rows, headers), "bank31.csv")
    assert view["table_summary"]["profile"] == "funds-trace-table-v81"
    assert view["table_summary"]["currency_totals"] == expected
    job = complete(client, case, view)
    assert job["stage"] == "done", job

    records = client.get(f"/api/v1/cases/{case}/imports/{view['import_id']}/records").json()["items"]
    assert [item["reason_code"] for item in records] == (
        ["transaction_valid"] * 3 + ["unknown_unit", "currency_unit_conflict"]
        if money_header == "交易金额" else ["transaction_valid"] * 4 + ["currency_unit_conflict"])
    assert all(item["flow_id"] is not None for item in records[:3])
    assert records[-1]["flow_id"] is None
    assert records[2]["details"]["currency_basis"] == "采用来源金额单位中的币种声明"
    fields = {item["column"]: item["raw"] for item in json.loads(records[1]["details"]["source_field_evidence"])}
    assert (fields[money_header], fields["金额单位"], fields["交易币种"]) == (source_amounts[1], units[1], "")


@pytest.mark.parametrize("filename,headers,old_profile", [
    ("bank28.csv", feedback_import.COLUMNS, "funds-trace-table-v69"),
    ("bank31.csv", BANK31_COLUMNS, "funds-trace-table-v78"),
    ("bank31.zip", BANK31_COLUMNS, "funds-trace-table-archive-v66"),
])
def test_frozen_bank_preview_receipt_and_package_keep_their_own_interpretation(
        client, database, make_case, monkeypatch, filename, headers, old_profile):
    raw = payload([row(**{"交易币种": "", "交易金额": "1.23"})], headers)
    if filename.endswith(".zip"):
        raw = archive([("synthetic-bank.csv", raw)])
    case = make_case("SYN frozen bank amount defaults")
    base = f"/api/v1/cases/{case}"
    parse = imports._parse

    def historical(*args, **options):
        return parse(*args, **{**options, "table_profile": old_profile, "_auto_event_profile": False})

    with monkeypatch.context() as frozen:
        frozen.setattr(imports, "_parse", historical)
        old = preview(client, case, raw, filename)
    assert old["table_summary"]["profile"] == old_profile and old["stats"]["ok"] == 0
    old_url = base + "/imports/" + old["import_id"]
    import_preview_cache.discard(old["import_id"])
    old_again = client.post(old_url + "/preview", json={"mode": old["mode"], "mapping": old["mapping"]})
    assert old_again.status_code == 200, old_again.text
    old_again = old_again.json()
    assert old_again["table_summary"]["profile"] == old_profile and old_again["stats"]["ok"] == 0
    old_job = complete(client, case, old_again)
    assert old_job["stage"] == "done" and old_job["result"]["ok"] == 0
    old_record, = client.get(old_url + "/records").json()["items"]
    assert (old_record["reason_code"], old_record["flow_id"]) == ("invalid_currency", None)
    assert client.get(base + "/stats").json()["import_coverage"]["unresolved_records"] == 1

    new = preview(client, case, raw, filename)
    import_preview_cache.discard(new["import_id"])
    new_job = complete(client, case, new)
    assert new_job["stage"] == "done" and new_job["result"]["ok"] == 1, new_job
    assert client.get(old_url).json()["result"] == old_job["result"]
    assert client.get(old_url + "/records").json()["items"] == [old_record]
    stats = client.get(base + "/stats").json()
    assert [(item["currency"], item["amount"]) for item in stats["amount_totals"]] == [("CNY", "1.23")]
    assert stats["import_coverage"]["unresolved_records"] == 0

    original = client.get(f"/api/cases/{case}/export")
    assert original.status_code == 200, original.text
    restored = client.post("/api/cases/import", files={"file": ("bank.fundcase", original.content)})
    assert restored.status_code == 200, restored.text
    restored_case = restored.json()["case_id"]
    provenance = database.q_raw("select source_id,parser_version from import_sources where case_id=?",
                                [restored_case])
    sources = {source["parser_version"]: source["source_id"] for source in provenance}
    new_profile = new["table_summary"]["profile"]
    assert set(sources) == {old_profile, new_profile}
    scoped = f"/api/v1/cases/{restored_case}/sources/"
    restored_old, = client.get(scoped + sources[old_profile] + "/report").json()["items"]
    restored_new, = client.get(scoped + sources[new_profile] + "/report").json()["items"]
    original_new, = client.get(base + "/sources/" + new["import_id"] + "/report").json()["items"]
    assert (restored_old["reason_code"], restored_old["details"]) == ("invalid_currency", old_record["details"])
    assert (restored_new["reason_code"], restored_new["details"]) == ("transaction_valid", original_new["details"])
    assert client.get(f"/api/v1/cases/{restored_case}/stats").json()["amount_total"] == "1.23"


def test_non_bank_payment_table_keeps_its_unit_and_currency_rules(client, make_case):
    original = payload([
        {"date": "2026-01-01", "payer_account": "SYN-A", "payee_account": "SYN-B", "amount": "1.23",
         "currency": "", "金额单位": "元", "channel": "WeChat Pay"},
        {"date": "2026-01-02", "payer_account": "SYN-A", "payee_account": "SYN-B", "amount": "2.34",
         "currency": "", "金额单位": "", "channel": "Alipay"},
    ], ("date", "payer_account", "payee_account", "amount", "currency", "金额单位", "channel"))
    case = make_case("SYN non-bank payment currency rules")
    view = preview(client, case, original, "payments.csv")
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"
    assert view["table_summary"]["currency_totals"] == {"CNY": "1.23"}
    job = complete(client, case, view)
    assert job["stage"] == "done", job
    records = client.get(f"/api/v1/cases/{case}/imports/{view['import_id']}/records").json()["items"]
    assert [item["reason_code"] for item in records] == ["transaction_valid", "unknown_unit"]
    assert "银行" not in records[0]["details"]["currency_basis"]
    assert all("amount_unit_basis" not in item["details"] for item in records)


def test_bank_default_evidence_keeps_literal_header_and_blank_currency_before_long_note(client, make_case):
    amount = "  交易金额（分）  "
    headers = [amount if field == "交易金额" else field for field in feedback_import.COLUMNS]
    original = payload([row(**{amount: "1234", "摘要说明": "x" * 1670, "交易币种": ""})], headers)
    case = make_case("SYN bank amount field evidence budget")
    view = preview(client, case, original, "bank28.csv")
    assert view["table_summary"]["currency_totals"] == {"CNY": "12.34"}
    job = complete(client, case, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    source = f"/api/v1/cases/{case}/sources/{view['import_id']}"
    record, = client.get(source + "/report").json()["items"]
    assert record["details"]["source_field_evidence_limited"] == "true"
    evidence = {item["column"]: item["raw"] for item in json.loads(record["details"]["source_field_evidence"])}
    assert {column: evidence.get(column) for column in (amount, "交易币种")} == {
        amount: "1234", "交易币种": ""}
    assert client.get(source + "/file").content == original


def test_bank_balance_currency_does_not_declare_transaction_currency(client, make_case):
    balance_header = "交易余额（USD）"
    headers = [balance_header if field == "交易余额" else field for field in BANK31_COLUMNS]
    amount = "12.34"
    original = payload([
        row(**{"交易币种": "", "交易金额": amount, balance_header: "100.00", "交易流水号": "SYN-NO-TRADE-CURRENCY"}),
        row(**{"交易币种": "USD", "交易金额": amount, balance_header: "100.00", "交易流水号": "SYN-TRADE-USD"}),
    ], headers)
    case = make_case("SYN unit scope stays in its field")
    view = preview(client, case, original, "bank31.csv")
    assert view["table_summary"]["currency_totals"] == {"USD": "12.34"}
    job = complete(client, case, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    source = f"/api/v1/cases/{case}/sources/{view['import_id']}"
    records = client.get(source + "/report").json()["items"]
    assert [(record["reason_code"], record["flow_id"] is not None) for record in records] == [
        ("currency_unit_conflict", False), ("transaction_valid", True)]


@pytest.mark.parametrize("form", ["csv", "xlsx", "zip"])
def test_literal_preamble_currency_and_units_apply_to_native_bank_amount_only(client, database, make_case, form):
    headers = BANK31_COLUMNS
    rows = [
        row(**{"交易流水号": "SYN-DECLARED", "交易金额": "1234", "交易余额": "100.00", "交易币种": ""}),
        row(**{"交易流水号": "SYN-CONFLICT", "交易金额": "250", "交易余额": "110.00", "交易币种": "JPY"}),
    ]
    actual = [{name: record.get(name, "") for name in headers} for record in rows]
    if form in {"csv", "zip"}:
        data = io.StringIO(newline="")
        writer = csv.writer(data)
        declarations = [("币种：美元",), ("金额单位：分",)] if form == "csv" else [("币种", "USD"), ("金额单位", "分")]
        writer.writerows(declarations)
        writer.writerow(headers)
        writer.writerows(record.values() for record in actual)
        original = data.getvalue().encode("utf-8-sig")
        if form == "zip":
            original = archive([("synthetic-bank.csv", original)])
    else:
        workbook = Workbook()
        workbook.active.append(["币种", "USD"])
        workbook.active.append(["金额单位", "分"])
        workbook.active.append(headers)
        for record in actual:
            workbook.active.append(list(record.values()))
        data = io.BytesIO()
        workbook.save(data)
        workbook.close()
        original = data.getvalue()

    case = make_case("SYN bank preamble source declarations")
    view = preview(client, case, original, f"bank31.{form}")
    assert view["table_summary"]["currency_totals"] == {"USD": "12.34"}
    assert view["table_summary"]["unresolved"] == 1
    job = complete(client, case, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    base = f"/api/v1/cases/{case}"
    flow, = client.get(base + "/flows", params={"granularity": "observations"}).json()["items"]
    assert (flow["trade_no"], flow["currency"], flow["amount"], flow["balance"]) == (
        "SYN-DECLARED", "USD", "12.34", "100.00")
    source = base + "/sources/" + view["import_id"]
    records = client.get(source + "/report").json()["items"]
    assert [(r["source_row"], r["reason_code"], r["flow_id"] is not None) for r in records[-2:]] == [
        (4, "transaction_valid", True), (5, "invalid_currency", False)]
    details = records[-2]["details"]
    evidence = json.loads(details["source_field_evidence"])
    assert next(item["raw"] for item in evidence if item["column"] == "交易币种") == ""
    assert {(item["role"], item["source_row"], item["header_row"], item["raw"]) for item in evidence
            if item["role"] in {"source_currency_declaration", "source_amount_unit_declaration"}} == {
        ("source_currency_declaration", 1, 3, "币种：美元" if form == "csv" else "USD"),
        ("source_amount_unit_declaration", 2, 3, "金额单位：分" if form == "csv" else "分"),
    }
    assert client.get(source + "/file").content == original


    package = client.get(f"/api/cases/{case}/export")
    assert package.status_code == 200, package.text
    restored = client.post("/api/cases/import", files={"file": ("preamble.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    restored_case = restored.json()["case_id"]
    restored_id, = database.q_raw("select source_id from import_sources where case_id=?", [restored_case])
    restored_report = client.get(f"/api/v1/cases/{restored_case}/sources/{restored_id['source_id']}/report")
    assert restored_report.status_code == 200
    assert restored_report.json()["items"][-2]["details"] == details


def test_preamble_unit_conflict_scope_and_frozen_parser(client, make_case, monkeypatch):
    workbook = Workbook()
    declared = workbook.active
    declared.title = "SYN declared segment"
    declared.append(["币种", "USD"])
    declared.append(["金额单位", "分"])
    declared.append(["交易金额（元）" if name == "交易金额" else name for name in BANK31_COLUMNS])
    explicit_yuan = row(**{"交易流水号": "SYN-UNIT-CONFLICT", "交易金额": "100.00", "交易币种": ""})
    declared.append([explicit_yuan.get(name, "") for name in BANK31_COLUMNS])
    unrelated = workbook.create_sheet("SYN independent sheet")
    unrelated.append(BANK31_COLUMNS)
    no_declaration = row(**{"交易流水号": "SYN-MEMO", "交易金额": "5.67", "交易币种": "", "备注": "币种：HKD"})
    unrelated.append([no_declaration.get(name, "") for name in BANK31_COLUMNS])
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    original = stream.getvalue()

    cid = make_case("SYN bank statement scope")
    view = preview(client, cid, original, "SYN-USD-bank31.xlsx")
    assert view["table_summary"]["currency_totals"] == {"CNY": "5.67"}
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    base = f"/api/v1/cases/{cid}/sources/{view['import_id']}/report"
    records = client.get(base).json()["items"]
    assert [(r["source_row"], r["reason_code"]) for r in records[-2:]] == [
        (4, "conflicting_unit"), (2, "transaction_valid")]

    parse = imports._parse

    def historical(*args, **options):
        return parse(*args, **{**options, "table_profile": "funds-trace-table-v78", "_auto_event_profile": False})

    other = make_case("SYN frozen declaration behavior")
    with monkeypatch.context() as frozen:
        frozen.setattr(imports, "_parse", historical)
        view = preview(client, other, original, "SYN-USD-bank31.xlsx")
    assert view["table_summary"]["profile"] == "funds-trace-table-v78"
    assert view["table_summary"]["currency_totals"] == {}
    job = complete(client, other, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 0
    old = client.get(f"/api/v1/cases/{other}/sources/{view['import_id']}/report").json()["items"]
    assert [(r["source_row"], r["reason_code"]) for r in old[-2:]] == [(4, "invalid_currency"), (2, "invalid_currency")]


def test_frozen_archive_missing_currency_can_be_supplemented_by_exact_preamble(client, make_case, monkeypatch):
    data = io.StringIO(newline="")
    bank_row = row(**{"交易流水号": "SYN-FORMERLY-MISSING", "交易金额": "45.67", "交易币种": ""})
    csv.writer(data).writerows([["币种", "USD"], BANK31_COLUMNS, [bank_row.get(name, "") for name in BANK31_COLUMNS]])
    original = archive([("declared-bank.csv", data.getvalue().encode("utf-8-sig"))])
    cid = make_case("SYN archived preamble reassessment")
    base = f"/api/v1/cases/{cid}"
    parse = imports._parse

    def historical(*args, **options):
        return parse(*args, **{**options, "table_profile": "funds-trace-table-archive-v66", "_auto_event_profile": False})

    with monkeypatch.context() as frozen:
        frozen.setattr(imports, "_parse", historical)
        old = preview(client, cid, original, "history.zip")
    assert old["table_summary"]["profile"] == "funds-trace-table-archive-v66"
    old_job = complete(client, cid, old)
    assert old_job["stage"] == "done" and old_job["result"]["ok"] == 0
    journal = base + f"/sources/{old['import_id']}/report"
    prior = client.get(journal).json()
    assert prior["items"][-1]["details"]["validation_issues"] == "invalid_currency"

    current = preview(client, cid, original, "history.zip")
    assert current["table_summary"]["currency_totals"] == {"USD": "45.67"}
    current_job = complete(client, cid, current)
    assert current_job["stage"] == "done" and current_job["result"]["ok"] == 1
    assert client.get(journal).json() == prior
    assert client.get(base + f"/imports/{old['import_id']}").json()["result"] == old_job["result"]
    stats = client.get(base + "/stats").json()
    assert [(item["currency"], item["amount"]) for item in stats["amount_totals"]] == [("USD", "45.67")]
    assert stats["import_coverage"]["unresolved_records"] == 0
