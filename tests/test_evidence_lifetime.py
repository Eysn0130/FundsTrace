"""Preview state must be released before the same input is parsed for commit."""
from contextlib import contextmanager
import gc
import io
import json
import time
import weakref

import pytest

from app import evidence_resolution as e, feedback_import, table_archive, table_cancellations, table_import
from app.services import imports
from test_folder_evidence import account, complete, preview, table, transaction
from test_table_cancellations import round_trip, source
from test_table_archive_v4 import envelope
from test_table_import import csv_plan


class Plan(dict):
    pass


class Evidence(list):
    pass


class Sentinel:
    pass


class SourcePath(str):
    pass


@pytest.mark.parametrize("broken", [False, True])
def test_csv_sampling_buffers_close_before_the_next_full_text_copy(monkeypatch, broken):
    original, references = io.StringIO, []
    def open_text(*args, **kwargs):
        assert all(reference() is None or reference().closed for reference in references)
        stream = original(*args, **kwargs)
        references.append(weakref.ref(stream))
        return stream
    monkeypatch.setattr(table_import.io, "StringIO", open_text)
    raw = b'date,payer_account,payee_account,amount\n2026-01-01,SYN-A,SYN-B,1.00\n'
    if broken:
        raw += b'"unterminated'
        with pytest.raises(table_import.TableImportError, match="CSV"):
            table_import._csv(raw, ".csv", table_import.EVIDENCE_PROFILE)
    else:
        assert len(table_import._csv(raw, ".csv", table_import.EVIDENCE_PROFILE)[0]["rows"]) == 2
    assert all(reference() is None or reference().closed for reference in references)


def test_parsed_cells_are_released_before_interpreting_the_next_record(monkeypatch):
    read, interpret = table_import._csv, table_import._interpret_candidates
    references = []

    def observe_read(*args, **kwargs):
        sheets = read(*args, **kwargs)
        for sheet in sheets:
            sheet["rows"][0].append("Synthetic unused column")
            for index, row in enumerate(sheet["rows"][1:]):
                cell = SourcePath(f"Synthetic unused value {index}")
                references.append(weakref.ref(cell))
                row.append(cell)
        return sheets

    calls = 0
    def observe_interpret(*args, **kwargs):
        nonlocal calls
        if calls:
            assert references[calls - 1]() is None
        calls += 1
        return interpret(*args, **kwargs)

    monkeypatch.setattr(table_import, "_csv", observe_read)
    monkeypatch.setattr(table_import, "_interpret_candidates", observe_interpret)
    with without_cycle_collection():
        plan = table_import.parse(source(round_trip())[0], suffix=".csv", profile=table_import.EVIDENCE_PROFILE)
        assert calls == 3 and len(plan["rows"]) == 2
        assert all(reference() is None for reference in references)


def test_cancellation_check_releases_consumed_source_cells(monkeypatch):
    read, observe = table_import._csv, table_cancellations._observation
    payload = source(round_trip())[0]
    plan = table_import.parse(payload, suffix=".csv", profile=table_import.EVIDENCE_ARCHIVE_PROFILE,
                              reference_policy=table_import.TRANSACTION_REFERENCE_POLICY)
    references = []

    def capture(*args, **kwargs):
        sheets = read(*args, **kwargs)
        for sheet in sheets:
            unused = sheet["rows"][0].index("IP地址")
            for index, row in enumerate(sheet["rows"][1:]):
                cell = SourcePath(f"Synthetic unused IP text {index}")
                references.append(weakref.ref(cell))
                row[unused] = cell
        return sheets

    calls = 0
    def check(*args, **kwargs):
        nonlocal calls
        if calls:
            assert references[calls - 1]() is None
        calls += 1
        return observe(*args, **kwargs)

    monkeypatch.setattr(table_import, "_csv", capture)
    monkeypatch.setattr(table_cancellations, "_observation", check)
    with without_cycle_collection():
        table_cancellations.apply(plan, payload, allow_feedback28=True)
        assert calls == 3 and len(plan["cancellations"]) == 1
        assert all(reference() is None for reference in references)
        assert table_cancellations.validate_evidence(plan["records"]) == plan["cancellations"]


def test_empty_history_does_not_index_every_new_transaction(database, make_case):
    class UnvisitedRows(list):
        def __iter__(self):
            raise AssertionError("No stored history needs a whole-input position index")

    cid = make_case("Synthetic empty history")
    plan = table_archive.parse(io.BytesIO(envelope([("rows.csv", source(round_trip())[0])])),
                               profile=table_archive.EVIDENCE_PROFILE)
    imports._bind_archive_header_history(cid, plan, UnvisitedRows(plan["rows"]), plan["stats"],
                                        {"sha256": "0" * 64, "filename": "synthetic.zip"})


@pytest.mark.parametrize("excel", [False, True])
def test_consumed_cells_keep_reordered_headers_and_exact_endpoint_evidence(excel):
    first = ["date", "payer_account", "payee_account", "amount", "currency"]
    records = [["2026-01-01 10:00:00", "SYN-A", "SYN-B", "10.25", "CNY"],
               ["date", "payee_account", "payer_account", "amount", "currency"],
               ["2026-01-02 10:00:00", "SYN-C", "SYN-D", "20.03", "USD"]]
    if excel:
        from openpyxl import Workbook
        workbook = Workbook()
        for row in [first, *records]:
            workbook.active.append(row)
        output = io.BytesIO()
        workbook.save(output)
        workbook.close()
        plan = table_import.parse(output.getvalue(), suffix=".xlsx", profile=table_import.EVIDENCE_PROFILE)
    else:
        plan = csv_plan(first, records, profile=table_import.EVIDENCE_PROFILE)
    assert plan["documents"][0]["header_rows"] == [1, 3]
    assert [record["reason_code"] for record in plan["records"]] == ["transaction_valid", "repeated_header", "transaction_valid"]
    assert [(row["payer_account"], row["payee_account"]) for row in plan["rows"]] == [("SYN-A", "SYN-B"), ("SYN-D", "SYN-C")]
    assert plan["stats"]["currency_totals"] == {"CNY": "10.25", "USD": "20.03"}
    for record, payer, payee in ((plan["records"][0], "SYN-A", "SYN-B"), (plan["records"][2], "SYN-D", "SYN-C")):
        data = e.validate_evidence(record["details"][e.DETAIL_FIELD])
        assert data["endpoints"]["payer"]["account"] == payer
        assert data["endpoints"]["payee"]["account"] == payee


@pytest.mark.parametrize("legacy", [False, True])
def test_directory_preview_releases_financial_staging_before_resolution(client, make_case, monkeypatch, legacy):
    cid = make_case("Synthetic preview staging")
    resolve, references = e.preview, []

    def observe(plan):
        assert plan["rows"] == [] and plan.get("cancelled_rows", []) == []
        references.append(len(plan["records"]))
        return resolve(plan)

    monkeypatch.setattr(e, "preview", observe)
    view = preview(client, cid, [("tx.csv", table("transaction", [transaction()])),
                                 ("acc.csv", table("account", [account()]))], legacy=legacy)
    assert references and view["stats"]["ok"] == 1
    assert len(view["preview"]) == 1 and view["self_accounts_in_file"][0]["count"] == 1
    summary = view.get("table_summary") or view["feedback"]
    assert summary["accepted"] == 1 and view["resolution"]["entities"] == 2
    job = complete(client, cid, view, legacy=legacy)
    assert job["stage"] == "done" and job["result"]["ok"] == 1


@contextmanager
def without_cycle_collection():
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.enable()
        # Test-only cleanup also releases the intentionally failing old classes.
        gc.collect()


def test_nonempty_preview_drops_its_complete_plan_without_waiting_for_gc():
    def invoke():
        plan = Plan(table_import.parse(source(round_trip())[0], suffix=".csv", profile=table_import.EVIDENCE_PROFILE))
        assert len(plan["records"]) == 3 and len(plan["rows"]) == 2
        reference = weakref.ref(plan)
        result = e.preview(plan)
        assert result["evidence_records"] == 2 and result["entities"] > 0
        return reference
    with without_cycle_collection():
        reference = invoke()
        assert reference() is None


def test_contextual_pass_drops_its_input_evidence_without_waiting_for_gc():
    def invoke():
        plan = table_import.parse(source(round_trip())[0], suffix=".csv", profile=table_import.EVIDENCE_PROFILE)
        record = plan["records"][0]
        data = json.loads(record["details"][e.DETAIL_FIELD])
        data.update(_family="a" * 64, _member_path=record["member_path"])
        evidence = Evidence([("synthetic-scope", 1, 1, data)])
        reference = weakref.ref(evidence)
        assert list(e.contextualize(evidence))
        return reference
    with without_cycle_collection():
        reference = invoke()
        assert reference() is None


def test_public_directory_preview_releases_plan_before_commit_reparse(client, make_case, monkeypatch):
    cid = make_case("Synthetic preview object lifetime")
    parse, references = imports._parse, []
    def observe(*args, **kwargs):
        # The next parse must not overlap the previous endpoint's whole plan.
        assert all(reference() is None for reference in references)
        parsed = parse(*args, **kwargs)
        frame = parsed[0]
        for key in ("feedback_plan", "table_plan"):
            if key in frame.attrs:
                plan = Plan(frame.attrs[key])
                frame.attrs[key] = plan
                references.append(weakref.ref(plan))
        return parsed
    monkeypatch.setattr(imports, "_parse", observe)
    with without_cycle_collection():
        view = preview(client, cid, [("tx.csv", table("transaction", [transaction()])),
                                     ("acc.csv", table("account", [account()]))])
        assert len(references) == 1 and references[0]() is None
        result = complete(client, cid, view)
        assert result["stage"] == "done" and result["result"]["ok"] == 1
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and references[-1]() is not None:
            time.sleep(.005)
        assert len(references) == 2 and all(reference() is None for reference in references)


@pytest.mark.parametrize("profile", [table_archive.PROFILE, table_archive.EVIDENCE_PROFILE])
@pytest.mark.parametrize("fail", [False, True])
def test_nested_archive_releases_merged_plan_on_success_and_error(monkeypatch, profile, fail):
    parse, references = table_import.parse, []
    def observe(*args, **kwargs):
        part = parse(*args, **kwargs)
        sentinel = Sentinel()
        part["members"][0]["_test_lifetime_sentinel"] = sentinel
        references.append(weakref.ref(sentinel))
        return part
    monkeypatch.setattr(table_import, "parse", observe)
    members = [("rows.csv", source(round_trip())[0])]
    if fail:
        members.append(("../rejected.csv", b"synthetic unsafe member"))
    raw = envelope(members)
    def invoke():
        if fail:
            try:
                table_archive.parse(io.BytesIO(raw), profile=profile)
            except table_import.TableImportError:
                return
            raise AssertionError("Unsafe nested member must fail")
        plan = table_archive.parse(io.BytesIO(raw), profile=profile)
        assert len(plan["records"]) == 3 and plan["cancellations"]
    with without_cycle_collection():
        invoke()
        assert references and all(reference() is None for reference in references)


@pytest.mark.parametrize("payloads", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_feedback_family_releases_found_bytes_and_paths_on_success_and_error(monkeypatch, payloads, fail):
    path, references = table_archive._path, []
    def observe(name):
        value = path(name)
        if value.endswith(".zip"):
            value = SourcePath(value)
            references.append(weakref.ref(value))
        return value
    monkeypatch.setattr(table_archive, "_path", observe)
    content = table("transaction", [transaction()])
    members = [("rows.csv", content)]
    if fail:
        members.append(("../rejected.csv", b"synthetic unsafe member"))
    raw = envelope(members)
    def invoke():
        stream = io.BytesIO(raw)
        if fail:
            try:
                feedback_import._family_members(stream, payloads=payloads)
            except (feedback_import.FeedbackImportError, table_import.TableImportError):
                assert stream.tell() == 0
                return
            raise AssertionError("Unsafe feedback member must fail")
        result = feedback_import._family_members(stream, payloads=payloads)
        assert result == [("synthetic-bank.zip/rows.csv", content if payloads else None, "synthetic-bank.zip")]
        assert stream.tell() == 0
    with without_cycle_collection():
        invoke()
        assert references and all(reference() is None for reference in references)

def test_auxiliary_preview_passes_do_not_decode_transaction_notes(monkeypatch):
    payload = envelope([("rows.csv", source(round_trip())[0]),
                        ("accounts.csv", table("account", [account()]))])
    plan = table_archive.parse(io.BytesIO(payload), profile=table_archive.EVIDENCE_PROFILE)
    expected = e.preview(plan)
    pool = plan["records"][0]["details"]._pool
    decode, calls = pool._decode, []
    transaction_payloads = {record["details"]._payload for record in plan["records"]
                            if record["disposition"] != "auxiliary"}
    def observe(payload, size):
        if payload in transaction_payloads:
            calls.append(True)
        return decode(payload, size)
    monkeypatch.setattr(pool, "_decode", observe)
    evidence = e._PreviewEvidence(plan)
    for _ in range(2):
        assert len(list(evidence.auxiliary())) == 1
    assert not calls
    assert e.preview(plan) == expected
    assert calls  # The complete pass still reads the transaction evidence.


@pytest.mark.parametrize("disposition", ["auxiliary", "accepted"])
@pytest.mark.parametrize("damage", ["compressed_notes", "identity_schema"])
def test_preview_rejects_damaged_evidence_after_auxiliary_filtering(disposition, damage):
    payload = envelope([("rows.csv", source(round_trip())[0]),
                        ("accounts.csv", table("account", [account()]))])
    plan = table_archive.parse(io.BytesIO(payload), profile=table_archive.EVIDENCE_PROFILE)
    record = next(record for record in plan["records"] if record["disposition"] == disposition)
    if damage == "compressed_notes":
        record["details"]._payload += b"unexpected trailing bytes"
    else:
        record["details"][e.DETAIL_FIELD] = '{"role":"transaction"}'
    with pytest.raises(ValueError):
        e.preview(plan)
