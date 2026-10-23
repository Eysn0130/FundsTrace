"""Synthetic public imports must verify persisted facts before durable success."""
import csv
import datetime as dt
from decimal import Decimal
import io
import zipfile

from openpyxl import Workbook
import pytest

from test_automatic_table_import import preview
from test_feedback_import_api import complete


FIELDS = (
    "trade_no", "trade_time", "payer_name", "payer_account", "payer_bank",
    "payee_name", "payee_account", "payee_bank", "amount", "balance", "currency",
    "summary", "channel", "self_account", "self_name",
)
TABLES = ("flows", "batches", "import_sources", "flow_sources", "import_members", "import_records", "main_accounts")


def row(number="SYN-NEW", **changes):
    result = {
        "trade_no": number, "trade_time": "2026-02-01 12:30:45.123456",
        "payer_name": "Synthetic payer", "payer_account": "000SYN-PAYER", "payer_bank": "Synthetic payer bank",
        "payee_name": "Synthetic payee", "payee_account": "000SYN-PAYEE", "payee_bank": "Synthetic payee bank",
        "amount": "123.45", "balance": "9876.54", "currency": "CNY", "summary": "Synthetic purpose",
        "channel": "Synthetic channel", "self_account": "000SYN-PAYER", "self_name": "Synthetic owner",
    }
    result.update(changes)
    return result


def csv_data(rows, fields=FIELDS):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(fields)
    writer.writerows([[record[field] for field in fields] for record in rows])
    return stream.getvalue().encode()


def envelope(payload):
    output = io.BytesIO()
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w"):
        pass
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("synthetic-transactions.csv", payload)
        package.writestr("synthetic-nested.zip", nested.getvalue())
    return output.getvalue()


def persisted(database):
    return {table: database.q_raw(f"select * from {table} order by all") for table in TABLES}


MUTATIONS = [
    ("amount", "123.46"), ("balance", "9876.55"), ("trade_time", dt.datetime(2026, 2, 1, 12, 30, 45, 123457)),
    ("currency", "USD"), ("trade_no", "SYN-WRONG-REFERENCE"),
    ("payer_name", "Synthetic changed payer"), ("payer_account", "000SYN-WRONG-PAYER"),
    ("payer_bank", "Synthetic changed payer bank"), ("payee_name", "Synthetic changed payee"),
    ("payee_account", "000SYN-WRONG-PAYEE"), ("payee_bank", "Synthetic changed payee bank"),
    ("summary", "Synthetic changed purpose"), ("channel", "Synthetic changed channel"),
    ("self_account", "000SYN-WRONG-OWNER"), ("self_name", "Synthetic changed owner"),
]


@pytest.mark.parametrize("field,value", MUTATIONS, ids=[entry[0] for entry in MUTATIONS])
def test_successful_database_write_with_changed_fact_is_rolled_back_and_retryable(
        client, database, make_case, monkeypatch, field, value):
    cid = make_case("Synthetic persistence verification")
    old = preview(client, cid, csv_data([row("SYN-OLD", amount="1.01")]))
    old_job = complete(client, cid, old)
    assert old_job["stage"] == "done"
    before = persisted(database)
    view = preview(client, cid, csv_data([row()]))
    write = database.insert_dataframe

    def corrupt(table, frame, columns):
        if table == "flows":
            frame = frame.copy(deep=True)
            frame.loc[frame.index[0], field] = value
        return write(table, frame, columns)

    with monkeypatch.context() as context:
        context.setattr(database, "insert_dataframe", corrupt)
        failed = complete(client, cid, view)
    assert failed["stage"] == "error", "An acknowledged write cannot establish that the expected facts were stored"
    assert failed["error_code"] == "import_integrity_failed"
    assert failed["result"] is None
    assert persisted(database) == before
    assert client.get(f"/api/v1/cases/{cid}/imports/{old['import_id']}").json()["result"] == old_job["result"]
    assert complete(client, cid, view)["stage"] == "done"
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid])[0]["n"] == 2


@pytest.mark.parametrize("fault", ["swapped", "missing", "other_case"])
def test_wrong_source_link_is_not_a_successful_import(client, database, make_case, monkeypatch, fault):
    cid = make_case("Synthetic source link verification")
    other = make_case("Synthetic unrelated link target")
    unrelated = complete(client, other, preview(client, other, csv_data([row("SYN-UNRELATED")])))
    assert unrelated["stage"] == "done"
    other_flow = database.q_raw("select id from flows where case_id=?", [other])[0]["id"]
    before = persisted(database)
    view = preview(client, cid, csv_data([row("SYN-FIRST"), row("SYN-SECOND", amount="234.56")]))
    write = database.insert_dataframe

    def corrupt(table, frame, columns):
        if table == "flow_sources":
            frame = frame.copy(deep=True)
            if fault == "swapped":
                frame["flow_id"] = list(reversed(frame["flow_id"].tolist()))
            else:
                frame.loc[frame.index[0], "flow_id"] = other_flow if fault == "other_case" else 9_000_000_000_000_000
        return write(table, frame, columns)

    with monkeypatch.context() as context:
        context.setattr(database, "insert_dataframe", corrupt)
        failed = complete(client, cid, view)
    assert failed["stage"] == "error"
    assert failed["error_code"] == "import_integrity_failed"
    assert persisted(database) == before
    assert complete(client, cid, view)["stage"] == "done"


def test_ordinary_fact_duplicate_preserves_its_established_self_field_contract(client, database, make_case):
    cid = make_case("Synthetic ordinary duplicate contract")
    assert complete(client, cid, preview(client, cid, csv_data([row()])))["stage"] == "done"
    duplicate = row(self_account="000SYN-ALTERNATE-OWNER", self_name="Synthetic alternate owner")
    job = complete(client, cid, preview(client, cid, csv_data([duplicate])))
    assert job["stage"] == "done"
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (0, 1)
    actual = database.q_raw("select self_account,self_name from flows where case_id=?", [cid])
    assert actual == [{"self_account": "000SYN-PAYER", "self_name": "Synthetic owner"}]


def test_ordinary_duplicate_must_still_link_to_the_correct_financial_facts(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic ordinary duplicate link verification")
    payload = csv_data([row("SYN-DUP-A"), row("SYN-DUP-B", amount="234.56")])
    first = preview(client, cid, payload)
    first_job = complete(client, cid, first)
    assert first_job["stage"] == "done" and first_job["result"]["ok"] == 2
    before = persisted(database)
    view = preview(client, cid, payload, "repeat.csv")
    write = database.insert_dataframe

    def swap(table, frame, columns):
        if table == "flow_sources":
            frame = frame.copy(deep=True)
            assert set(frame["disposition"]) == {"duplicate"}
            frame["flow_id"] = list(reversed(frame["flow_id"].tolist()))
        return write(table, frame, columns)

    with monkeypatch.context() as context:
        context.setattr(database, "insert_dataframe", swap)
        failed = complete(client, cid, view)
    assert failed["stage"] == "error" and failed["error_code"] == "import_integrity_failed"
    assert persisted(database) == before
    assert client.get(f"/api/v1/cases/{cid}/imports/{first['import_id']}").json()["result"] == first_job["result"]
    retry = complete(client, cid, view)
    assert retry["stage"] == "done" and (retry["result"]["ok"], retry["result"]["rows_dup"]) == (0, 2)


def test_archive_cannot_link_equal_visible_facts_to_a_different_source_owner(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic archive owner verification")
    different_owner = row(self_account="000SYN-ALTERNATE-OWNER", self_name="Synthetic alternate owner")
    assert complete(client, cid, preview(client, cid, csv_data([different_owner])))["stage"] == "done"
    existing = database.q_raw("select id from flows where case_id=?", [cid])[0]["id"]
    before = persisted(database)
    view = preview(client, cid, envelope(csv_data([row()])), "synthetic.zip")
    write = database.insert_dataframe

    def wrong_owner(table, frame, columns):
        if table == "flow_sources":
            frame = frame.copy(deep=True)
            frame.loc[frame.index[0], "flow_id"] = existing
            frame.loc[frame.index[0], "disposition"] = "duplicate"
        return write(table, frame, columns)

    with monkeypatch.context() as context:
        context.setattr(database, "insert_dataframe", wrong_owner)
        failed = complete(client, cid, view)
    assert failed["stage"] == "error"
    assert failed["error_code"] == "import_integrity_failed"
    assert persisted(database) == before
    retry = complete(client, cid, view)
    assert retry["stage"] == "done" and retry["result"]["ok"] == 1
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid])[0]["n"] == 2


def test_undetermined_financial_meaning_is_retained_without_blocking_storage(client, database, make_case):
    cid = make_case("Synthetic source uncertainty is not write corruption")
    payload = ("交易时间,本方账号,对方账号,交易金额,收付标志,交易币种,交易是否成功\n"
               "2026-02-01 10:00:00,000SYN-A,000SYN-B,1.01,出,CNY,1\n"
               "2026-02-01 11:00:00,000SYN-A,000SYN-C,-2.02,出,CNY,1\n"
               "2026-02-01 12:00:00,000SYN-A,000SYN-D,3.03,,CNY,1\n"
               "2026-02-01 13:00:00,000SYN-A,000SYN-E,4.04,出,CNY,02\n").encode()
    view = preview(client, cid, payload)
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    summary = job["result"]["table_summary"]
    assert (summary["record_total"], summary["accepted"], summary["unresolved"]) == (4, 1, 3)
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    assert [record["disposition"] for record in records] == ["imported", "unresolved", "unresolved", "unresolved"]
    assert {record["reason_code"] for record in records[1:]} == {"negative_amount", "missing_direction", "unknown_status"}
    assert database.q_raw("select amount from flows where case_id=?", [cid]) == [{"amount": Decimal("1.01")}]


CHINESE = dict(zip(FIELDS, (
    "交易流水号", "交易日期时间", "付款人名称", "付款人账号", "付款人开户行", "收款人名称", "收款人账号", "收款人开户行",
    "交易金额", "交易后余额", "交易币种", "交易摘要", "交易渠道", "本方账号", "本方户名",
)))


@pytest.mark.parametrize("kind,unit,factor", [
    ("csv", "元", Decimal(1)), ("csv", "分", Decimal("0.01")), ("csv", "万元", Decimal(10000)),
    ("xlsx", "元", Decimal(1)), ("xlsx", "分", Decimal("0.01")), ("xlsx", "万元", Decimal(10000)),
])
def test_equivalent_layouts_and_exact_units_persist_the_same_facts(client, database, make_case, kind, unit, factor):
    cid = make_case("Synthetic layout metamorphism")
    good = [row("SYN-LAYOUT-A", amount="12345.67", balance="998765.43"),
            row("SYN-LAYOUT-B", amount="1.23", balance="998764.20", trade_time="2026-02-01 13:30:45.123456")]
    expected = []
    for item in good:
        expected.append({**item, "trade_time": dt.datetime.fromisoformat(item["trade_time"]),
                         "amount": Decimal(item["amount"]), "balance": Decimal(item["balance"])})
    bad = row("SYN-LAYOUT-BAD", amount="not-money")
    fields = tuple(reversed(FIELDS))
    headers = [CHINESE[field] + (f"({unit})" if field in {"amount", "balance"} else "") for field in fields]
    values = []
    for item in [*good, bad]:
        values.append([format(Decimal(item[field]) / factor, "f") if field in {"amount", "balance"} and item[field] != "not-money"
                       else item[field] for field in fields])
    if kind == "xlsx":
        workbook = Workbook()
        workbook.active.append(headers)
        for cells in values:
            workbook.active.append(cells)
        output = io.BytesIO()
        workbook.save(output)
        payload = output.getvalue()
    else:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(values)
        payload = output.getvalue().encode()
    view = preview(client, cid, payload, f"synthetic.{kind}")
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert (job["result"]["ok"], job["result"]["table_summary"]["unresolved"]) == (2, 1)
    actual = database.q_raw(f"select {','.join(FIELDS)} from flows where case_id=? order by trade_no", [cid])
    assert actual == expected
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    assert len(records) == 3 and records[2]["reason_code"] == "invalid_amount"
    repeated = complete(client, cid, preview(client, cid, payload, f"repeat.{kind}"))
    assert repeated["stage"] == "done" and (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 2)
    assert database.q_raw(f"select {','.join(FIELDS)} from flows where case_id=? order by trade_no", [cid]) == expected
