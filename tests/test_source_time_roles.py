"""Literal public regressions for transaction and posting time provenance."""
import csv
import io
import json

import pytest

from app import table_import
from app.services import imports
from test_folder_evidence import archive, complete


def payload(times):
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows([
        [*times, "payer account", "payee account", "amount", "currency", "交易流水号"],
        [*times.values(), "SYN-TIME-A", "SYN-TIME-B", "10.01", "CNY", "SYN-TIME-ROLE"],
    ])
    return stream.getvalue().encode("utf-8-sig")


def upload(client, cid, data, route="single"):
    base = f"/api/v1/cases/{cid}/imports"
    if route == "directory":
        response = client.post(base + "/directory/preview", files=[("files", ("time.csv", data))])
    else:
        response = client.post(base + "/preview", files={"file": (
            "time.zip" if route == "zip" else "time.csv",
            archive([("time.csv", data)]) if route == "zip" else data)})
    assert response.status_code == 200, response.text
    return response.json()


def journal(client, cid, view):
    response = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records")
    assert response.status_code == 200, response.text
    return [row for row in response.json()["items"] if row["source_row"] == 2][0]


@pytest.mark.parametrize("route", ["single", "directory", "zip"])
@pytest.mark.parametrize("reverse", [False, True])
def test_transaction_time_is_not_conflicted_by_independent_posting_time(client, make_case, route, reverse):
    times = {"交易时间": "2026-01-02 23:59:59.123456", "记账时间": "2026-01-03 02:03:04.000005"}
    if reverse:
        times = dict(reversed(list(times.items())))
    cid = make_case("Synthetic independent time roles")
    view = upload(client, cid, payload(times), route)
    assert view["stats"]["ok"] == 1
    assert complete(client, cid, view)["stage"] == "done"
    page = client.get(f"/api/v1/cases/{cid}/flows").json()
    assert page["total"] == 1
    row = page["items"][0]
    assert row["trade_time"].replace("T", " ") == "2026-01-02 23:59:59.123456"
    note = json.loads(journal(client, cid, view)["details"]["source_time_evidence"])
    assert note["ordinary_time_role"] == "transaction"
    assert {(x["role"], x["raw"]) for x in note["values"]} == {
        ("transaction", "2026-01-02 23:59:59.123456"), ("posting", "2026-01-03 02:03:04.000005")}
    assert all(x.get("utc") is None for x in note["values"])


@pytest.mark.parametrize("route", ["single", "directory", "zip"])
def test_posting_only_does_not_become_transaction_time(client, make_case, route):
    cid = make_case("Synthetic posting only")
    view = upload(client, cid, payload({"记账时间": "2026-01-03 02:03:04.000005"}), route)
    assert view["stats"]["ok"] == 0
    assert complete(client, cid, view)["stage"] == "done"
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0
    record = journal(client, cid, view)
    assert record["disposition"] == "unresolved" and record["reason_code"] == "unsupported_time_role"
    evidence = json.loads(record["details"]["source_time_evidence"])
    assert evidence["ordinary_time_role"] is None
    assert evidence["values"][0]["role"] == "posting"
    assert evidence["values"][0]["raw"] == "2026-01-03 02:03:04.000005"


@pytest.mark.parametrize("value,utc,offset", [
    ("2026-02-03T00:10:00.123456+08:00", "2026-02-02T16:10:00.123456Z", "+08:00"),
    ("2026-02-02T16:10:00.123456Z", "2026-02-02T16:10:00.123456Z", "Z"),
    ("2026-02-02T12:00:00.000001-05:00", "2026-02-02T17:00:00.000001Z", "-05:00"),
])
def test_explicit_offset_enters_flow_with_separate_wall_clock_and_instant(client, make_case, value, utc, offset):
    cid = make_case("Synthetic explicit time instant")
    view = upload(client, cid, payload({"交易时间": value}))
    assert view["stats"]["ok"] == 1
    assert complete(client, cid, view)["stage"] == "done"
    record = journal(client, cid, view)
    assert record["reason_code"] == "transaction_valid"
    evidence = json.loads(record["details"]["source_time_evidence"])
    assert evidence["ordinary_time_role"] == "transaction"
    item = evidence["values"][0]
    assert (item["raw"], item["offset"], item["utc"]) == (value, offset, utc)
    assert item["basis"] == "explicit_offset"
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 1


def test_naive_time_has_no_inferred_absolute_instant(client, make_case):
    cid = make_case("Synthetic no offset")
    view = upload(client, cid, payload({"交易时间": "2026-02-03 00:10:00.123456"}))
    assert view["stats"]["ok"] == 1
    assert complete(client, cid, view)["stage"] == "done"
    value = json.loads(journal(client, cid, view)["details"]["source_time_evidence"])["values"][0]
    assert value["basis"] == "source_wall_clock" and value["utc"] is None and value["offset"] is None


@pytest.mark.parametrize("header", ["业务时间", "时间", "date"])
def test_generic_time_header_does_not_gain_transaction_role(client, make_case, header):
    cid = make_case("Synthetic unspecified source time")
    view = upload(client, cid, payload({header: "2026-02-03 00:10:00.123456"}))
    assert view["stats"]["ok"] == 1  # Preserve the established generic-table interpretation.
    assert complete(client, cid, view)["stage"] == "done"
    evidence = json.loads(journal(client, cid, view)["details"]["source_time_evidence"])
    assert evidence["ordinary_time_role"] == "unspecified"
    assert evidence["values"][0]["role"] == "unspecified"
    assert evidence["values"][0]["column"] == header


def test_unspecified_business_time_cannot_override_explicit_transaction(client, make_case):
    cid = make_case("Synthetic independent business time")
    view = upload(client, cid, payload({"交易时间": "2026-01-02 23:59:59.123456", "业务时间": "2026-01-03 02:03:04.000005"}))
    assert view["stats"]["ok"] == 1
    assert complete(client, cid, view)["stage"] == "done"
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]["trade_time"].replace("T", " ") == "2026-01-02 23:59:59.123456"
    evidence = json.loads(journal(client, cid, view)["details"]["source_time_evidence"])
    assert [item["role"] for item in evidence["values"]] == ["transaction", "unspecified"]


def test_two_explicit_transaction_times_still_conflict(client, make_case):
    cid = make_case("Synthetic contradictory transaction times")
    view = upload(client, cid, payload({"交易时间": "2026-01-02 23:59:59.123456", "交易日期时间": "2026-01-03 02:03:04.000005"}))
    assert view["stats"]["ok"] == 0
    assert complete(client, cid, view)["stage"] == "done"
    assert journal(client, cid, view)["reason_code"] == "conflicting_time"


def test_mapping_explicit_receipt_time_cannot_make_it_transaction_time(client, make_case):
    cid = make_case("Synthetic explicit receipt time")
    view = upload(client, cid, payload({"受理时间": "2026-01-03 02:03:04.000005"}))
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={"mode": "A", "mapping": {
        "trade_time": "受理时间", "payer_account": "payer account", "payee_account": "payee account", "amount": "amount", "currency": "currency"}})
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_mapping"
    assert view["stats"]["ok"] == 0
    assert complete(client, cid, view)["stage"] == "done"
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0


def test_closed_receipt_declaration_is_scoped_to_its_own_row(client, make_case):
    statement = "本文件交易时间表示受理时间，不是交易完成时间或记账时间。"
    data = list(csv.reader(io.StringIO(payload({"交易时间": "2026-02-03 10:00:00", "备注": statement}).decode("utf-8-sig"))))
    second = data[1].copy(); second[1] = ""; second[-1] = "SYN-TIME-OTHER-ROW"
    stream = io.StringIO(newline=""); csv.writer(stream).writerows([*data, second])
    content = stream.getvalue().encode("utf-8-sig")
    cid = make_case("Synthetic row-scoped source time declaration")
    view = upload(client, cid, content)
    assert view["stats"]["ok"] == 1 and view["stats"]["unresolved"] == 1
    assert complete(client, cid, view)["stage"] == "done"
    record = journal(client, cid, view)
    assert record["reason_code"] == "unsupported_time_role"
    assert json.loads(record["details"]["source_time_evidence"])["values"][0]["role"] == "receipt"
    assert any(item["role"] == "row_time_role_declaration" and item["raw"] == statement
               for item in json.loads(record["details"]["source_field_evidence"]))
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]["trade_no"] == "SYN-TIME-OTHER-ROW"
    historical = table_import.parse(content, suffix=".csv", profile=table_import.OPTIONAL_REFERENCE_PROFILE)
    assert len(historical["rows"]) == 2


def test_invalid_offset_preserves_raw_without_instant(client, make_case):
    cid = make_case("Synthetic invalid offset")
    raw = "2026-02-03T00:10:00+25:00"
    view = upload(client, cid, payload({"交易时间": raw}))
    assert view["stats"]["ok"] == 0
    assert complete(client, cid, view)["stage"] == "done"
    record = journal(client, cid, view)
    assert record["reason_code"] == "invalid_time"
    value = json.loads(record["details"]["source_time_evidence"])["values"][0]
    assert value["raw"] == raw and value["utc"] is None


def test_ready_historical_posting_source_keeps_original_parser(client, make_case, monkeypatch):
    cid = make_case("Synthetic historical time parser")
    data = payload({"记账时间": "2026-01-03 02:03:04.000005"})
    with monkeypatch.context() as selection:
        selection.setattr(table_import, "PROFILE", table_import.OPTIONAL_REFERENCE_PROFILE)
        selection.setattr(imports, "TABLE_PROFILE", table_import.OPTIONAL_REFERENCE_PROFILE)
        prior = upload(client, cid, data)
    assert prior["stats"]["ok"] == 1
    first = complete(client, cid, prior)
    assert first["stage"] == "done"
    old_record = journal(client, cid, prior)
    current = upload(client, cid, data)
    assert current["stats"]["ok"] == 0
    assert complete(client, cid, current)["stage"] == "done"
    assert journal(client, cid, prior) == old_record
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 1
    assert client.delete(f"/api/batches/{first['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0
    package = client.get(f"/api/cases/{cid}/export")
    assert package.status_code == 200
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/v1/cases/{restored.json()['case_id']}/flows").json()["total"] == 0
