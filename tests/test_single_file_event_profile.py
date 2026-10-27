"""Fresh ordinary uploads admit only explicitly declared event contracts."""
import csv
import io
import json
from decimal import Decimal

from openpyxl import Workbook
import pytest

from app import event_resolution, evidence_resolution, table_import
from app.services import imports
from test_event_import import transaction
from test_folder_evidence import complete
from xls_fixtures import workbook as xls_workbook


def matrix(rows):
    headers = list(rows[0])
    return [headers, *[[row.get(header, "") for header in headers] for row in rows]]


def payload(sheets, suffix):
    if suffix == ".xls":
        return xls_workbook(sheets)
    if suffix == ".xlsx":
        book = Workbook()
        book.remove(book.active)
        for name, rows in sheets:
            sheet = book.create_sheet(name)
            for row in rows:
                sheet.append(row)
        output = io.BytesIO()
        book.save(output)
        book.close()
        return output.getvalue()
    output = io.StringIO(newline="")
    delimiter, encoding = {".csv": (",", "utf-8-sig"), ".tsv": ("\t", "utf-16"),
                           ".txt": (";", "gb18030")}[suffix]
    csv.writer(output, delimiter=delimiter).writerows(sheets[0][1])
    return output.getvalue().encode(encoding)


def upload(client, cid, content, suffix):
    return client.post(f"/api/v1/cases/{cid}/imports/preview",
                       files={"file": ("synthetic-event" + suffix, content)})


def ordinary(own, **changes):
    return {key: value for key, value in transaction(own, **changes).items()
            if key not in event_resolution.HEADERS.values()}


def source_notes(database, cid):
    return [json.loads(row["details_json"]) for row in database.q_raw(
        "select details_json from import_records where case_id=? order by source_id,record_no", [cid])]


def assert_event_pair(client, database, cid):
    stats = client.get(f"/api/v1/cases/{cid}/stats").json()
    funds = stats["funds"]
    assert funds["status"] == "exact"
    assert funds["observation_count"] == 2 and funds["confirmed_event_count"] == 1
    assert funds["count"] == {"lower": 1, "upper": 1}
    assert funds["totals"] == [{"currency": "CNY", "amount": "100.00", "lower": "100.00", "upper": "100.00"}]
    observations = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()["items"]
    assert len(observations) == 2
    assert {row["balance"] for row in observations} == {"50.00", "200.00"}
    assert all(row["amount"] == "100.00" for row in observations)
    defaults = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert len(defaults) == 1 and defaults[0]["balance"] is None
    assert defaults[0]["amount"] == "100.00"
    assert database.q_raw("select amount,balance from flows where case_id=? order by balance", [cid]) == [
        {"amount": Decimal("100.00"), "balance": Decimal("50.00")},
        {"amount": Decimal("100.00"), "balance": Decimal("200.00")}]
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"] for row in observations]}).status_code == 200
    edges = client.get("/api/graph", params={"case_id": cid}).json()["edges"]
    assert len(edges) == 1 and edges[0]["cnt"] == 1 and edges[0]["total"] == "100.00"


@pytest.mark.parametrize("suffix", [".csv", ".tsv", ".txt", ".xlsx"])
def test_fresh_single_file_contract_selects_v20_once_and_confirms_event(
        client, database, make_case, monkeypatch, suffix):
    cid = make_case("Synthetic ordinary entry explicit event")
    content = payload([("Both ledgers", matrix([transaction("SYN-A"), transaction("SYN-B")]))], suffix)
    reader_name = "_excel" if suffix == ".xlsx" else "_csv"
    reader, reads = getattr(table_import, reader_name), []
    def observed(*args, **kwargs):
        reads.append(reader_name)
        return reader(*args, **kwargs)
    monkeypatch.setattr(table_import, reader_name, observed)
    response = upload(client, cid, content, suffix)
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["table_summary"]["profile"] == table_import.EVIDENCE_PROFILE
    assert reads == [reader_name], "Profile admission must reuse the one decoded source"
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert job["result"]["ok"] == 2
    assert reads == [reader_name, reader_name]
    assert all(event_resolution.DETAIL_FIELD in notes for notes in source_notes(database, cid))
    assert_event_pair(client, database, cid)


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_fresh_plain_table_with_ledger_fields_keeps_v17(client, database, make_case, suffix):
    cid = make_case("Synthetic ordinary entry stays ordinary")
    content = payload([("Ordinary ledgers", matrix([ordinary("SYN-A"), ordinary("SYN-B")]))], suffix)
    response = upload(client, cid, content, suffix)
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["table_summary"]["profile"] == table_import.CURRENCY_REGION_PROFILE
    assert complete(client, cid, view)["stage"] == "done"
    assert event_resolution.summary(cid) is None
    assert all(event_resolution.DETAIL_FIELD not in notes for notes in source_notes(database, cid))
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid]) == [{"n": 2}]


@pytest.mark.parametrize("schema", ["incomplete", "duplicate", "data_values"])
def test_contract_words_only_admit_unique_complete_real_headers(client, make_case, schema):
    cid = make_case("Synthetic false event contract")
    values = matrix([transaction("SYN-A"), transaction("SYN-B")])
    if schema == "incomplete":
        index = values[0].index("参考号业务域")
        values = [row[:index] + row[index + 1:] for row in values]
    elif schema == "duplicate":
        values = [row + [row[values[0].index("参考号机构")]] for row in values]
    else:
        rows = [ordinary("SYN-A"), ordinary("SYN-B")]
        words = [*event_resolution.HEADERS.values(), "交易时间", "交易金额", "付款账号", "收款账号"]
        for row in rows:
            row.update({f"Synthetic note {index}": word for index, word in enumerate(words)})
        values = matrix(rows)
    response = upload(client, cid, payload([("Not a contract", values)], ".csv"), ".csv")
    assert response.status_code == 200, response.text
    assert response.json()["table_summary"]["profile"] == table_import.CURRENCY_REGION_PROFILE


@pytest.mark.parametrize("layout", ["later_region", "later_sheet"])
def test_later_real_contract_selects_one_profile_before_all_rows(client, database, make_case, layout):
    cid = make_case("Synthetic later explicit event contract")
    first = matrix([ordinary("SYN-A", **{"交易流水号": "SYN-ORDINARY", "交易金额": "7.00"})])
    second = matrix([transaction("SYN-A"), transaction("SYN-B")])
    # A later header can reorder all coordinates; admission must use the same
    # actual regional transitions as interpretation, not search arbitrary cells.
    second = [list(reversed(row)) for row in second]
    suffix = ".csv" if layout == "later_region" else ".xlsx"
    sheets = [("Mixed regions", first + second)] if layout == "later_region" else [("Plain first", first), ("Contract second", second)]
    response = upload(client, cid, payload(sheets, suffix), suffix)
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["table_summary"]["profile"] == table_import.EVIDENCE_PROFILE
    assert complete(client, cid, view)["stage"] == "done"
    notes = source_notes(database, cid)
    financial = [note for note in notes if "flow_sha256" in note]
    assert len(financial) == 3
    assert all(evidence_resolution.DETAIL_FIELD in note for note in financial)
    assert sum(event_resolution.DETAIL_FIELD in note for note in financial) == 2
    funds = client.get(f"/api/v1/cases/{cid}/stats").json()["funds"]
    assert funds["count"] == {"lower": 1, "upper": 2}
    assert funds["totals"] == [{"currency": "CNY", "amount": None, "lower": "100.00", "upper": "107.00"}]


def historical_preview(client, cid, content, monkeypatch):
    parse = imports._parse
    def historical(*args, **kwargs):
        # Model an existing ready session created before fresh-header admission.
        # The replay contract remains the persisted v17, not this test wrapper.
        kwargs.pop("_auto_event_profile", None)
        return parse(*args, **kwargs)
    with monkeypatch.context() as prior:
        prior.setattr(imports, "_parse", historical)
        response = upload(client, cid, content, ".csv")
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["table_summary"]["profile"] == table_import.CURRENCY_REGION_PROFILE
    return view


def test_old_ready_session_stays_pinned_and_historical_facts_block_upgrade(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic explicit event historical pin")
    content = payload([("Both ledgers", matrix([transaction("SYN-A"), transaction("SYN-B")]))], ".csv")
    view = historical_preview(client, cid, content, monkeypatch)
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview",
                           json={"mode": view["mode"], "mapping": view["mapping"]})
    assert response.status_code == 200, response.text
    assert response.json()["table_summary"]["profile"] == table_import.CURRENCY_REGION_PROFILE
    job = complete(client, cid, response.json())
    assert job["stage"] == "done" and job["result"]["ok"] == 2
    assert event_resolution.summary(cid) is None
    assert all(event_resolution.DETAIL_FIELD not in notes for notes in source_notes(database, cid))
    facts = database.q_raw("select * from flows where case_id=? order by id", [cid])
    sessions = database.q_raw("select * from import_sessions where case_id=? order by import_id", [cid])
    fresh = upload(client, cid, content, ".csv")
    assert fresh.status_code == 409, fresh.text
    assert fresh.json()["error"]["code"] == "historical_evidence_overlap"
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == facts
    assert database.q_raw("select * from import_sessions where case_id=? order by import_id", [cid]) == sessions


def test_explicit_event_columns_do_not_override_xls_v18(client, database, make_case):
    cid = make_case("Synthetic legacy XLS event columns")
    content = payload([("Both ledgers", matrix([transaction("SYN-A"), transaction("SYN-B")]))], ".xls")
    response = upload(client, cid, content, ".xls")
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["table_summary"]["profile"] == table_import.XLS_PROFILE
    assert complete(client, cid, view)["stage"] == "done"
    assert event_resolution.summary(cid) is None
    assert all(event_resolution.DETAIL_FIELD not in notes for notes in source_notes(database, cid))
