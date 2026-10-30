"""Synthetic original-byte ingress tests; timestamps are never seeded in the DB."""
import datetime as dt
import hashlib
import io
import json
import zipfile

import pytest

from app import feedback_import, table_archive, table_import
from test_feedback_import_api import archive as feedback_zip, preview as zip_preview
from test_folder_evidence import account, archive, complete, preview, scoped, table, transaction


VALID = ["2026-01-01 10:00:00", "2026-01-01 10:00:00.1", "2026-01-01 10:00:00.123",
         "2026-01-01 10:00:00.123456", "2026-01-01 10:00:00.100000", "2026-01-01 10:00:00.123457"]
INVALID = ["2026-01-01 10:00:00.1234567", "2026-01-01 10:00:00.", "2026-02-30 10:00:00.1",
           "2026-01-01 10:00:60.1", "2026-01-01T10:00:00.1", "2026-01-01 10:00:00.1Z",
           "2026-01-01 10:00:00.１２３", "1899-12-31 23:59:59.9", "2101-01-01 00:00:00.1"]


def original_rows(times):
    return [transaction(**{"交易时间": value, "交易流水号": f"SYN-TIME-{index}", "交易账号": "",
                           "交易余额": "", "交易金额": "10.25", "交易币种": "USD" if index % 2 else "CNY",
                           "收付标志": "进" if index % 2 else "出"}) for index, value in enumerate(times)]


def upload(client, cid, route, rows):
    if route == "feedback":
        content = feedback_zip(rows, account=False)
        return zip_preview(client, cid, content), content
    members = [("tx.csv", table("transaction", rows)), ("account.csv", table("account", [account()]))]
    members = scoped(members, "Synthetic bank")
    if route.startswith("table"):
        members.append(("readme.txt", b"Synthetic attachment, no transaction facts"))
    if route.endswith("zip"):
        content = archive(members)
        return zip_preview(client, cid, content), content
    return preview(client, cid, members, scope_bank=None), None


def flows(client, cid, granularity="observations"):
    response = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": granularity, "page_size": 200})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("route", ["feedback", "evidence-zip", "evidence-directory", "table-zip", "table-directory"])
def test_fractional_originals_public_ingress(client, database, make_case, route):
    cid = make_case("Synthetic fractional originals")
    view, content = upload(client, cid, route, original_rows(VALID + INVALID))
    assert view["stats"]["ok"] == len(VALID), view["stats"]
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert job["result"]["ok"] == len(VALID)
    page = flows(client, cid)
    assert page["total"] == len(VALID)
    actual = {row["trade_no"]: row for row in page["items"]}
    for index, value in enumerate(VALID):
        row = actual[f"SYN-TIME-{index}"]
        assert dt.datetime.fromisoformat(row["trade_time"]) == dt.datetime.fromisoformat(value)
        assert (row["amount"], row["balance"], row["currency"]) == ("10.25", None, "USD" if index % 2 else "CNY")
        side = "payee" if index % 2 else "payer"
        assert row[f"raw_{side}_account"] == "SYN-C1"
        assert row[f"{side}_source_account"] == ""
        assert row["batch_id"] == str(job["result"]["batch_id"])
        assert row["analysis_revision"] == page["analysis_revision"]
        if route != "feedback":
            assert row[f"{side}_card"] == "SYN-C1"
            assert row[f"{side}_display_account"] == "SYN-C1"
            assert row[f"{side}_card_state"] == "confirmed"
        sources = client.get(f"/api/v1/cases/{cid}/flows/{row['id']}/sources").json()["items"]
        assert len(sources) == 1 and sources[0]["member_row"] == index + 2
        assert sources[0]["source_id"] == view["import_id"]
    assert flows(client, cid, "events")["funds"] == page["funds"]
    assert page["funds"]["observation_count"] == len(VALID)
    assert page["funds"]["totals"] == [
        {"currency": currency, "amount": None, "lower": "0.00", "upper": "30.75"}
        for currency in ("CNY", "USD")]
    assert page["analysis_revision"]
    stats = client.get(f"/api/v1/cases/{cid}/stats").json()
    assert stats["funds"] == page["funds"] and stats["analysis_revision"] == page["analysis_revision"]
    source = view["import_id"]
    journal = client.get(f"/api/v1/cases/{cid}/imports/{source}/records").json()
    tx = [row for row in journal["items"] if row["member_path"].endswith(("transactions.csv", "交易明细信息.csv"))]
    assert len(tx) == len(VALID + INVALID)
    assert [row["disposition"] for row in tx] == ["imported"] * len(VALID) + ["unresolved"] * len(INVALID)
    assert all("invalid_time" in row["details"].get("validation_issues", "") for row in tx[len(VALID):])
    original = client.get(f"/api/v1/cases/{cid}/sources/{source}/file")
    assert original.status_code == 200
    if content is not None:
        assert original.content == content
    else:
        with zipfile.ZipFile(io.BytesIO(original.content)) as packed:
            assert packed.read("SYN-REQUEST/Synthetic bank交易明细信息.csv") == table("transaction", original_rows(VALID + INVALID))
    report = client.get(f"/api/v1/cases/{cid}/sources/{source}/report").json()
    assert report["sha256"] == hashlib.sha256(original.content).hexdigest()
    package = client.get(f"/api/cases/{cid}/export")
    assert package.status_code == 200, package.text
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    copy_id = restored.json()["case_id"]
    copied = flows(client, copy_id)
    assert copied["funds"] == page["funds"]
    fields = ("trade_no", "trade_time", "amount", "balance", "currency", "raw_payer_account", "raw_payee_account")
    assert sorted(tuple(row[k] for k in fields) for row in copied["items"]) == sorted(tuple(row[k] for k in fields) for row in page["items"])
    copied_source = client.get(f"/api/v1/cases/{copy_id}/flows/{copied['items'][0]['id']}/sources").json()["items"][0]["source_id"]
    copied_report = client.get(f"/api/v1/cases/{copy_id}/sources/{copied_source}/report").json()
    assert copied_report["sha256"] == report["sha256"]
    assert [(r["member_path"], r["source_row"], r["disposition"], r["reason_code"], r["details"]) for r in copied_report["items"]] == [(r["member_path"], r["source_row"], r["disposition"], r["reason_code"], r["details"]) for r in report["items"]]
    # Existing portable format carries source hashes/journals, not source files.
    assert client.get(f"/api/v1/cases/{copy_id}/sources/{copied_source}/file").status_code == 404


@pytest.mark.parametrize("old,new", [
    (feedback_import.CURRENCY_PROFILE, feedback_import.MICROSECOND_PROFILE),
    (feedback_import.EVIDENCE_PROFILE, feedback_import.MICROSECOND_EVIDENCE_PROFILE),
    (table_archive.EVIDENCE_PROFILE, table_archive.MICROSECOND_PROFILE),
    (table_archive.XLS_EVIDENCE_PROFILE, table_archive.MICROSECOND_XLS_PROFILE),
])
def test_old_profiles_reproduce_and_whole_second_facts_do_not_drift(old, new):
    content = feedback_zip(original_rows(VALID + INVALID), account=False)
    parser = feedback_import.parse if old in feedback_import.SUPPORTED_PROFILES else table_archive.parse
    before = parser(io.BytesIO(content), profile=old)
    after = parser(io.BytesIO(content), profile=new)
    try:
        assert len(before["rows"]) == 1 and len(after["rows"]) == 6
        assert before["rows"][0] == after["rows"][0]
        previous = dict(before["records"][0])
        previous["details"] = dict(previous["details"])
        if parser is table_archive.parse:
            previous["details"]["trade_no_basis"] = "source_transaction_id"
        assert previous == after["records"][0]
        # Feedback accepted identities include an identity digest; historical
        # unresolved rows never had that digest. Physical positions stay fixed.
        assert [(r["member_path"], r["source_row"]) for r in before["records"]] == [(r["member_path"], r["source_row"]) for r in after["records"]]
        assert all(r["disposition"] == "unresolved" for r in before["records"][1:])
    finally:
        for plan in (before, after):
            if plan.get("_details_pool"):
                plan["_details_pool"].close()


@pytest.mark.parametrize("route,old_profile", [
    ("feedback", feedback_import.CURRENCY_PROFILE),
    ("evidence-zip", feedback_import.EVIDENCE_PROFILE),
    ("table-zip", table_archive.EVIDENCE_PROFILE),
])
@pytest.mark.parametrize("remove_old_first", [False, True])
def test_authorized_cross_version_reimport_supports_withdrawal_and_reopen(
        client, database, make_case, monkeypatch, route, old_profile, remove_old_first):
    cid = make_case("Synthetic time version upgrade")
    with monkeypatch.context() as old:
        if route == "feedback":
            old.setattr(feedback_import, "PROFILE", old_profile)
        elif route == "evidence-zip":
            old.setattr(feedback_import, "MICROSECOND_EVIDENCE_PROFILE", old_profile)
        else:
            old.setattr(table_archive, "evidence_profile", lambda _: old_profile)
        prior, content = upload(client, cid, route, original_rows(VALID))
        assert prior["stats"]["ok"] == 1
        first = complete(client, cid, prior)
        assert first["stage"] == "done"
    source = prior["import_id"]
    prior_journal = client.get(f"/api/v1/cases/{cid}/imports/{source}/records").json()
    assert flows(client, cid)["total"] == 1  # Changing the selected version never replays old unresolved records.
    # Replay byte-identical original through the public upload/commit boundary.
    current = zip_preview(client, cid, content)
    second = complete(client, cid, current)
    assert second["stage"] == "done", second
    assert (second["result"]["ok"], second["result"]["rows_dup"]) == (5, 1)
    page = flows(client, cid)
    assert page["total"] == 6
    assert client.get(f"/api/v1/cases/{cid}/imports/{source}/records").json() == prior_journal
    whole = next(r for r in page["items"] if r["trade_no"] == "SYN-TIME-0")
    sources = client.get(f"/api/v1/cases/{cid}/flows/{whole['id']}/sources").json()["items"]
    assert {s["source_id"] for s in sources} == {source, current["import_id"]}
    assert len({s["parser_version"] for s in sources}) == 2
    for receipt in (first, second):
        assert client.get(f"/api/v1/cases/{cid}/imports/{receipt['import_id']}").json()["result"] == receipt["result"]
    remove, keep = (first, second) if remove_old_first else (second, first)
    assert client.delete(f"/api/batches/{remove['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    expected_count = 6 if remove_old_first else 1
    assert flows(client, cid)["total"] == expected_count
    package = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    assert flows(client, restored.json()["case_id"])["total"] == expected_count
    assert client.delete(f"/api/batches/{keep['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert flows(client, cid)["total"] == 0


@pytest.mark.parametrize("route", ["feedback", "evidence-directory", "table-directory"])
@pytest.mark.parametrize("equal,reverse", [(False, False), (False, True), (True, False), (True, True)])
def test_cancellation_uses_exact_microseconds_not_source_row_order(client, make_case, route, equal, reverse):
    from test_feedback_cancellations import round_trip
    rows = round_trip()
    for row, value in zip(rows, [".099999", ".1", ".100000" if equal else ".100001"]):
        row["交易时间"] = "2026-01-01 10:00:00" + value
    if reverse:
        rows.reverse()
    cid = make_case("Synthetic cancellation time precision")
    view, _ = upload(client, cid, route, rows)
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert flows(client, cid)["total"] == (2 if equal else 1)
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    assert sum(r["disposition"] == "cancelled" for r in records) == (0 if equal else 2)
    package = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    assert flows(client, restored.json()["case_id"])["funds"] == flows(client, cid)["funds"]


@pytest.mark.parametrize("right_time,confirmed", [(".100000", 1), (".100001", 0)])
def test_new_archive_event_reference_keeps_microsecond_distinctions(client, make_case, right_time, confirmed):
    from app import event_resolution
    from test_event_import import csv_rows, transaction as event_row
    from test_folder_evidence import accepted
    cid = make_case("Synthetic strong event time")
    members = [("sender.csv", csv_rows([event_row("SYN-A", **{"交易时间": "2026-01-01 10:00:00.1"})])),
               ("recipient.csv", csv_rows([event_row("SYN-B", **{"交易时间": "2026-01-01 10:00:00" + right_time})]))]
    view, result = accepted(client, cid, members, scope_bank=None)
    assert view["table_summary"]["profile"] == table_archive.MICROSECOND_PROFILE
    assert result["ok"] == 2
    assert event_resolution.summary(cid)["confirmed_events"] == confirmed
    assert flows(client, cid, "events")["total"] == 2 - confirmed
    package = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    assert event_resolution.summary(restored.json()["case_id"])["confirmed_events"] == confirmed


@pytest.mark.parametrize("route", ["feedback", "evidence-directory", "table-directory"])
def test_fractional_commit_failure_rolls_back_all_facts(client, database, make_case, monkeypatch, route):
    from test_table_write_integrity import persisted
    cid = make_case("Synthetic fractional rollback")
    before = persisted(database)
    view, _ = upload(client, cid, route, original_rows(VALID))
    write = database.insert_dataframe
    def fail_after_write(table_name, frame, columns):
        result = write(table_name, frame, columns)
        if table_name == "flows":
            raise RuntimeError("Synthetic failure after flow write")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(database, "insert_dataframe", fail_after_write)
        failed = complete(client, cid, view)
    assert failed["stage"] == "error" and failed["result"] is None
    assert persisted(database) == before
    assert complete(client, cid, view)["stage"] == "done"
    assert flows(client, cid)["total"] == len(VALID)


@pytest.mark.parametrize("profile,accepted", [(table_archive.XLS_EVIDENCE_PROFILE, 1), (table_archive.MICROSECOND_XLS_PROFILE, 6)])
def test_real_biff28_public_profile_and_portable_precision(client, make_case, monkeypatch, profile, accepted):
    from xls_fixtures import workbook
    rows = original_rows(VALID + INVALID)
    headers = list(feedback_import.COLUMNS)
    payload = workbook([("Synthetic feedback", [headers, *[[row.get(h, "") for h in headers] for row in rows]])])
    cid = make_case("Synthetic BIFF fractional time")
    with monkeypatch.context() as selection:
        selection.setattr(table_archive, "evidence_profile", lambda _: profile)
        view = preview(client, cid, [("feedback.xls", payload)], scope_bank=None)
        assert view["table_summary"]["profile"] == profile
        assert view["stats"]["ok"] == accepted
        result = complete(client, cid, view)
    assert result["stage"] == "done", result
    page = flows(client, cid)
    assert page["total"] == accepted
    assert {dt.datetime.fromisoformat(r["trade_time"]) for r in page["items"]} == {dt.datetime.fromisoformat(v) for v in VALID[:accepted]}
    package = client.get(f"/api/cases/{cid}/export")
    with zipfile.ZipFile(io.BytesIO(package.content)) as packed:
        meta = json.loads(packed.read("meta.json"))
        assert meta["format"] == 6 and meta["source_files"] == "not_included"
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    assert flows(client, restored.json()["case_id"])["funds"] == page["funds"]


@pytest.mark.parametrize("value", ["1900-01-01 00:00:00.000001", "2100-12-31 23:59:59.999999",
                                    "2000-02-29 00:00:00.12", "2026-01-01 00:00:00.1234", "2026-01-01 00:00:00.12345"])
def test_strict_fractional_range_boundaries(value):
    assert feedback_import._time(value, fractional=True) == dt.datetime.fromisoformat(value)
    assert feedback_import._time(value) is None
    assert feedback_import._time(value[:19], fractional=True) == feedback_import._time(value[:19])


@pytest.mark.parametrize("route", ["feedback", "evidence-directory", "table-directory"])
@pytest.mark.parametrize("invalid", ["2026-01-01 10:00:00.1000017", "2101-01-01 10:00:00.1", "2026-01-01T10:00:00.1"])
def test_invalid_cancellation_time_cannot_exclude_accepted_money(client, make_case, route, invalid):
    from test_feedback_cancellations import round_trip
    rows = round_trip()
    rows[0]["交易时间"], rows[1]["交易时间"], rows[2]["交易时间"] = VALID[0], VALID[1], invalid
    cid = make_case("Synthetic invalid cancellation time")
    view, _ = upload(client, cid, route, rows)
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert flows(client, cid)["total"] == 2
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    assert not any(r["disposition"] == "cancelled" for r in records)
