"""Chunked writes retain exact facts, source links and one public transaction."""
from decimal import Decimal
import time
import weakref

import pytest

from app import evidence_resolution, importer
from app.services import imports
from test_folder_evidence import account, complete, preview, table, transaction
from test_table_write_integrity import csv_data, row


AMOUNTS = [("900719925474099.91", "CNY"), ("0.01", "USD"), ("10.25", "CNY"),
           ("11.01", "USD"), ("2.00", "CNY")]
TABLES = ("flows", "batches", "import_sources", "flow_sources", "import_members", "import_records",
          "main_accounts", "evidence_sources", "resolution_entities", "resolved_endpoints",
          "resolution_subject_aliases", "resolution_state", "resolution_revisions")


class FingerprintTime(str):
    pass


def view(client, cid, directory):
    if directory:
        records = [transaction(**{"交易流水号": f"SYN-CHUNK-{index}", "交易金额": amount, "交易币种": currency,
                                  "交易余额": "9999999999999999.99"})
                   for index, (amount, currency) in enumerate(AMOUNTS)]
        records.append(dict(records[0]))
        return preview(client, cid, [("tx.csv", table("transaction", records)),
                                     ("acc.csv", table("account", [account()]))])
    records = [row(f"SYN-CHUNK-{index}", amount=amount, currency=currency, balance="9999999999999999.99")
               for index, (amount, currency) in enumerate(AMOUNTS)]
    records.append(dict(records[0]))
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("synthetic.csv", csv_data(records))})
    assert response.status_code == 200, response.text
    return response.json()


def snapshot(database, cid):
    return {name: database.q_raw(f"select * from {name} where case_id=? order by all", [cid]) for name in TABLES}


@pytest.mark.parametrize("directory", [False, True])
def test_public_chunked_writes_preserve_money_links_and_repeat_receipts(client, database, make_case, monkeypatch, directory):
    cid = make_case("Synthetic bounded import writes")
    monkeypatch.setattr(importer, "WRITE_CHUNK", 2, raising=False)
    monkeypatch.setattr(evidence_resolution, "WRITE_CHUNK", 2, raising=False)
    write, calls = database.insert_dataframe, []
    def bounded(name, frame, columns):
        if name in {"flows", "flow_sources", "resolution_entities", "resolved_endpoints", "resolution_subject_aliases"}:
            assert len(frame) <= 2, "Write parameters must be constructed within the chunk bound"
            calls.append((name, len(frame)))
        return write(name, frame, columns)
    monkeypatch.setattr(database, "insert_dataframe", bounded)
    first = complete(client, cid, view(client, cid, directory))
    assert first["stage"] == "done", first
    assert first["result"]["ok"] == (6 if directory else 5)
    assert first["result"]["rows_dup"] == (0 if directory else 1)
    assert sum(name == "flows" for name, _ in calls) == 3
    assert sum(name == "flow_sources" for name, _ in calls) == 3
    assert len(database.q_raw("select * from flow_sources where case_id=?", [cid])) == 6
    expected = list(AMOUNTS) + ([AMOUNTS[0]] if directory else [])
    totals = {}
    for amount, currency in expected:
        totals[currency] = totals.get(currency, Decimal(0)) + Decimal(amount)
    actual = database.q_raw("select currency,sum(amount) amount from flows where case_id=? group by currency", [cid])
    assert {item["currency"]: item["amount"] for item in actual} == totals
    assert {item["balance"] for item in database.q_raw("select balance from flows where case_id=?", [cid])} == {Decimal("9999999999999999.99")}
    original = database.q_raw("select * from flows where case_id=? order by id", [cid])
    revision = first["result"].get("resolution")
    if directory:
        projection = database.q_raw("select * from resolved_endpoints where case_id=? order by all", [cid])
        with database.transaction():
            assert evidence_resolution.rebuild(cid) == revision
        assert database.q_raw("select * from resolved_endpoints where case_id=? order by all", [cid]) == projection
    repeated = complete(client, cid, view(client, cid, directory))
    assert repeated["stage"] == "done", repeated
    assert (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 6)
    assert repeated["result"].get("resolution") == revision
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == original
    assert len(database.q_raw("select * from flow_sources where case_id=?", [cid])) == 12


@pytest.mark.parametrize("failed_table", ["flow_sources", "import_records", "resolved_endpoints"])
def test_second_write_chunk_failure_rolls_back_all_facts_and_retry_is_explicit(client, database, make_case, monkeypatch, failed_table):
    cid = make_case("Synthetic atomic chunk failure")
    monkeypatch.setattr(importer, "WRITE_CHUNK", 2, raising=False)
    monkeypatch.setattr(evidence_resolution, "WRITE_CHUNK", 2, raising=False)
    monkeypatch.setattr(imports, "JOURNAL_CHUNK_RECORDS", 2)
    previous = preview(client, cid, [("old.csv", table("transaction", [transaction(**{"交易流水号": "SYN-PRIOR", "交易金额": "1.01"})])),
                                     ("acc.csv", table("account", [account()]))])
    prior_job = complete(client, cid, previous)
    assert prior_job["stage"] == "done" and prior_job["result"]["ok"] == 1
    before = snapshot(database, cid)
    request = view(client, cid, True)
    write, count = database.insert_dataframe, 0
    def fail_second(name, frame, columns):
        nonlocal count
        if name == failed_table:
            count += 1
            if count == 2:
                raise RuntimeError("Synthetic second chunk interruption")
        return write(name, frame, columns)
    with monkeypatch.context() as failure:
        failure.setattr(database, "insert_dataframe", fail_second)
        result = complete(client, cid, request)
    assert count == 2 and result["stage"] == "error" and result["result"] is None
    assert snapshot(database, cid) == before
    retried = complete(client, cid, request)
    assert retried["stage"] == "done" and retried["result"]["ok"] == 6
    assert len(database.q_raw("select * from flow_sources where case_id=?", [cid])) == 7


def test_journal_byte_bound_releases_rows_and_preserves_every_original_detail(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic bounded journal JSON")
    original, captured, limits = imports._write_feedback_journal, {}, []
    def observe(case_id, source_id, plan):
        assert plan["profile"] in evidence_resolution.PROFILES and plan["rows"] == []
        expected = {record["record_no"]: imports._json(record["details"]) for record in plan["records"]}
        limit = max(len(value.encode("utf-8")) for value in expected.values()) + 1
        monkeypatch.setattr(imports, "JOURNAL_CHUNK_BYTES", limit)
        monkeypatch.setattr(imports, "JOURNAL_CHUNK_RECORDS", 100)
        captured.update(expected)
        limits.append(limit)
        return original(case_id, source_id, plan)
    write, batches = database.insert_dataframe, []
    def check(name, frame, columns):
        if name == "import_records":
            assert sum(len(value.encode("utf-8")) for value in frame["details_json"]) <= limits[-1]
            batches.append(len(frame))
        return write(name, frame, columns)
    monkeypatch.setattr(imports, "_write_feedback_journal", observe)
    monkeypatch.setattr(database, "insert_dataframe", check)
    request = view(client, cid, True)
    result = complete(client, cid, request)
    assert result["stage"] == "done" and result["result"]["feedback"]["accepted"] == 6
    assert len(batches) > 1 and sum(batches) == len(captured)
    stored = database.q_raw("select record_no,details_json from import_records where case_id=? and source_id=?", [cid, request["import_id"]])
    assert {record["record_no"]: record["details_json"] for record in stored} == captured


@pytest.mark.parametrize("selection", [None, [], ["SYN-A"]])
def test_early_row_release_preserves_main_selection_first_name_and_duplicates(client, database, make_case, selection):
    cid = make_case("Synthetic compact main declarations")
    records = [{"本方账号": "SYN-A", "本方户名": name, "本方开户银行": "Synthetic bank",
                "交易时间": "2026-01-01 10:00:00", "交易金额": "1.01", "收付标志": "出",
                "交易对手账卡号": "SYN-B", "交易币种": "CNY", "交易流水号": f"SYN-MAIN-{index}"}
               for index, name in enumerate(("Synthetic first name", "Synthetic second name"))]
    source = [("rows.csv", csv_data(records, fields=tuple(records[0])))]
    def run():
        request = preview(client, cid, source, scope_bank=None)
        url = f"/api/v1/cases/{cid}/imports/{request['import_id']}"
        response = client.post(url + "/commit", json={"mode": request["mode"], "mapping": request["mapping"], "mark_accounts": selection})
        assert response.status_code == 200, response.text
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            job = client.get(url).json()
            if job["stage"] in {"done", "error"}:
                assert job["stage"] == "done", job
                return job["result"]
            time.sleep(.005)
        raise AssertionError("synthetic import timed out")
    first = run()
    assert first["ok"] == 2 and first["table_summary"]["accepted"] == 2
    assert first["self_accounts"] == [{"account": "SYN-A", "name": "Synthetic first name"}]
    expected = [] if selection == [] else [{"account": "SYN-A", "name": "Synthetic first name", "source": "auto"}]
    assert database.q_raw("select account,name,source from main_accounts where case_id=?", [cid]) == expected
    before = database.q_raw("select * from main_accounts where case_id=?", [cid])
    repeated = run()
    assert (repeated["ok"], repeated["rows_dup"]) == (0, 2)
    assert repeated["self_accounts"] == first["self_accounts"]
    assert database.q_raw("select * from main_accounts where case_id=?", [cid]) == before


def test_funds_write_releases_exact_keys_after_source_ids_are_resolved(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic exact-key write lifetime")
    timestamp, references = importer._tkey, []
    def watch(value):
        result = FingerprintTime(timestamp(value))
        references.append(weakref.ref(result))
        return result
    monkeypatch.setattr(importer, "_tkey", watch)
    write, seen, live_keys = database.insert_dataframe, [], []
    def observe(name, frame, columns):
        if name == "flows":
            live_keys.append(sum(reference() is not None for reference in references))
            assert references and live_keys[-1] == 0
            seen.append(len(frame))
        return write(name, frame, columns)
    monkeypatch.setattr(database, "insert_dataframe", observe)
    first = complete(client, cid, view(client, cid, True))
    assert live_keys and max(live_keys) == 0
    assert first["stage"] == "done", first
    assert seen and first["result"]["ok"] == 6
    assert len(database.q_raw("select * from flow_sources where case_id=?", [cid])) == 6
    repeated = complete(client, cid, view(client, cid, True))
    assert repeated["stage"] == "done" and repeated["result"]["rows_dup"] == 6
    assert all(reference() is None for reference in references)
