"""Public synthetic cancellation proofs retain meaning across CSV containers."""
from decimal import Decimal
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app import case_packages, table_import
from app.services import imports
from test_archive_profile_upgrade import frozen_preview
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_cancellations import COLUMNS, round_trip, source, transaction
from test_table_container_identity import archive_bytes
from test_table_contract_lifecycle import facts, source_records
from test_table_currency_region_lifecycle import receipt, source_snapshot
from test_table_mapping_coverage import coverage
from test_table_mapping_versions import profile_preview
from test_table_write_integrity import persisted


V3 = "funds-trace-table-archive-v3"
V4 = "funds-trace-table-archive-v4"


def done(client, cid, view):
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    return job


def ingest(client, cid, content, filename="standalone.csv"):
    view = preview(client, cid, content, filename)
    return view, done(client, cid, view)


def raw(database, cid):
    return database.q_raw("select * from flows where case_id=? order by id", [cid])


def effective(database, cid):
    return database.q_raw(
        "select currency,count(*) n,sum(amount) amount from effective_flows where case_id=? group by currency order by currency", [cid])


def assert_effective(database, cid, count, amount):
    assert effective(database, cid) == [{"currency": "CNY", "n": count, "amount": Decimal(amount)}]


def assert_gap(client, cid):
    value = coverage(client, cid)
    assert value["unresolved_records"] > 0
    assert value["financial_interpretation_complete"] is False
    return value


def chain_rows():
    rows = round_trip()
    rows += [
        transaction(**{"交易时间": "2026-01-01 10:03:00", "交易金额": "20.00", "交易余额": "80.00", "交易流水号": "SYN-NEXT"}),
        transaction(**{"交易时间": "2026-01-01 10:04:00", "交易金额": "-20.00", "摘要说明": "撤销", "交易流水号": "SYN-NEXT"}),
    ]
    rows += [row | {"交易账号": "SYN-INDEPENDENT"} for row in round_trip()]
    return rows


def repreview(client, cid, view):
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={
        "mode": view["mode"], "mapping": view["mapping"]})
    assert response.status_code == 200, response.text
    refreshed = response.json()
    assert refreshed["table_summary"]["profile"] == view["table_summary"]["profile"]
    return refreshed


def summary_ready(client, cid, payload, monkeypatch, profile="funds-trace-table-v17"):
    view = profile_preview(client, cid, payload, monkeypatch, profile)
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={
        "mode": view["mode"], "mapping": {**view["mapping"], "summary": "IP地址"}})
    assert response.status_code == 200, response.text
    return response.json()


def historical_positive_negative_leg(database, cid, view, *, record_no=3):
    """Model an old stored interpretation, not a reachable current amount mapping.

    The public v5 source supplies valid physical provenance first. Like the
    strict historical-negative fixture in test_table_cancellation_package, this
    controlled legacy state adds a positive actual fact for the negative leg
    and binds its journal to that fact's complete digest.
    """
    fields = (*table_import.FLOW_FIELDS, "self_account", "self_name")
    original = database.q_raw(
        "select f.* from flows f join flow_sources fs on fs.case_id=f.case_id and fs.flow_id=f.id "
        "where fs.case_id=? and fs.source_id=? and fs.source_row=?", [cid, view["import_id"], record_no - 1])[0]
    leg = database.q_raw(
        "select * from import_records where case_id=? and source_id=? and record_no=?",
        [cid, view["import_id"], record_no])[0]
    actual = {field: original[field] for field in fields}
    actual.update(trade_time="2026-01-01 10:02:00", summary="冲正", balance=Decimal("100.00"))
    flow_id = database.q(
        f"insert into flows(case_id,{','.join(fields)}) values (?,{','.join('?' for _ in fields)}) returning id",
        [cid, *(actual[field] for field in fields)])[0]["id"]
    details = json.loads(leg["details_json"])
    details.pop("validation_issues", None)
    details.pop("validation_basis", None)
    details.update(financial_effect="source_reported", flow_sha256=table_import.flow_digest(actual))
    database.x(
        "update import_records set disposition='imported',reason_code='transaction_valid',details_json=? "
        "where case_id=? and source_id=? and record_no=?",
        [json.dumps(details, ensure_ascii=False), cid, view["import_id"], record_no])
    database.x(
        "insert into flow_sources(case_id,source_id,source_row,flow_id,disposition) values (?,?,?,?,'imported')",
        [cid, view["import_id"], record_no, flow_id])
    return flow_id


@pytest.mark.parametrize("nested", [False, True], ids=["flat-zip", "nested-zip"])
@pytest.mark.parametrize("archive_first", [False, True], ids=["csv-first", "archive-first"])
def test_identical_csv_and_renamed_archive_pair_share_effective_proof_without_rewriting_sources(
        client, database, make_case, archive_first, nested):
    cid = make_case("Synthetic same bytes cancellation container order")
    payload, content = source(round_trip(), path="bank/renamed.csv")
    if nested:
        content = archive_bytes([("bank/renamed.csv", payload)], nested=True)
    materials = [(payload, "standalone.csv"), (content, "synthetic.zip")]
    if archive_first:
        materials.reverse()
    first = preview(client, cid, *materials[0])
    assert first["automatic"] is True
    assert first["table_summary"]["profile"] == (
        "funds-trace-table-archive-v4" if archive_first else "funds-trace-table-v17")
    first_job = complete(client, cid, first)
    assert first_job["stage"] == "done", first_job
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    frozen = source_snapshot(database, cid, first["import_id"])
    first_records = source_records(client, cid, first["import_id"])

    second = preview(client, cid, *materials[1])
    assert second["automatic"] is True
    second_job = complete(client, cid, second)
    assert second_job["stage"] == "done", second_job
    after = database.q_raw("select * from flows where case_id=? order by id", [cid])
    prior_ids = {row["id"] for row in before}
    assert [row for row in after if row["id"] in prior_ids] == before
    assert len(after) == 2
    assert {row["amount"] for row in after} == {Decimal("1.00"), Decimal("10.00")}
    assert source_snapshot(database, cid, first["import_id"]) == frozen
    assert receipt(client, cid, first["import_id"]) == first_job["result"]
    assert source_records(client, cid, first["import_id"]) == first_records

    for view, (original, filename) in zip((first, second), materials):
        records = source_records(client, cid, view["import_id"])
        assert [record["source_row"] for record in records] == [2, 3, 4]
        path = "bank/renamed.csv"
        if nested:
            path = "synthetic-envelope.zip/" + path
        assert {record["member_path"] for record in records} == {path if filename.endswith(".zip") else "table.csv"}
        downloaded = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file")
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == original
        if filename.endswith(".zip"):
            assert [record["disposition"] for record in records[1:]] == ["cancelled", "cancelled"]
        else:
            assert records[2]["disposition"] == "unresolved"
            assert records[2]["reason_code"] == "negative_amount"
            assert records[2]["details"]["validation_issues"] == "negative_amount"

    # One observed payment remains: the paired ten-unit movement is excluded
    # without erasing its retained raw flow or changing the ordinary journal.
    assert database.q_raw(
        "select currency,count(*) n,sum(amount) amount from effective_flows where case_id=? group by currency", [cid]) == [
        {"currency": "CNY", "n": 1, "amount": Decimal("1.00")}]
    current = coverage(client, cid)
    assert current["unresolved_records"] == 0
    assert current["cancelled_records"] == 2
    assert current["financial_interpretation_complete"] is True


def test_standalone_csv_keeps_eleven_units_and_its_unproved_negative_record(client, database, make_case):
    cid = make_case("Synthetic ordinary negative has no cancellation proof")
    payload, _ = source(round_trip(), path="bank/renamed.csv")
    view, job = ingest(client, cid, payload)
    assert job["result"]["ok"] == 2
    assert_effective(database, cid, 2, "11.00")
    records = source_records(client, cid, view["import_id"])
    assert [record["disposition"] for record in records] == ["imported", "imported", "unresolved"]
    assert records[2]["details"]["validation_issues"] == "negative_amount"
    assert assert_gap(client, cid)["unresolved_records"] == 1


@pytest.mark.parametrize("archive_first", [False, True], ids=["csv-first", "v3-first"])
def test_v3_renamed_path_keeps_old_meaning_until_current_v4_is_appended(
        client, database, make_case, monkeypatch, archive_first):
    cid = make_case("Synthetic v3 cancellation container compatibility")
    payload, content = source(round_trip(), path="bank/renamed.csv")
    archive = frozen_preview(client, cid, content, monkeypatch, V3)
    ordinary = preview(client, cid, payload)
    ordered = [archive, ordinary] if archive_first else [ordinary, archive]
    receipts = {view["import_id"]: done(client, cid, view)["result"] for view in ordered}
    before = raw(database, cid)
    frozen = {view["import_id"]: source_snapshot(database, cid, view["import_id"]) for view in ordered}
    assert_effective(database, cid, 2, "11.00")
    old_gap = assert_gap(client, cid)
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert_effective(database, restored, 2, "11.00")
    assert coverage(client, restored) == old_gap
    current, job = ingest(client, cid, content, "current.zip")
    assert current["table_summary"]["profile"] == V4
    assert job["result"]["ok"] == 0
    assert raw(database, cid) == before
    for view in ordered:
        assert source_snapshot(database, cid, view["import_id"]) == frozen[view["import_id"]]
        assert receipt(client, cid, view["import_id"]) == receipts[view["import_id"]]
    assert_effective(database, cid, 1, "1.00")
    assert coverage(client, cid)["unresolved_records"] == 0


@pytest.mark.parametrize("filename", [
    "standalone.csv", "standalone.tsv", "standalone.txt", "standalone", "standalone.CSV", ".hidden", "ordinary."])
def test_every_existing_text_csv_filename_form_can_share_current_proof(
        client, database, make_case, filename):
    cid = make_case("Synthetic text CSV cancellation suffix")
    payload, content = source(round_trip(), path="bank/renamed.csv")
    ordinary, _ = ingest(client, cid, payload, filename)
    frozen = source_snapshot(database, cid, ordinary["import_id"])
    _, job = ingest(client, cid, content, "current.zip")
    assert job["result"]["ok"] == 0
    assert source_snapshot(database, cid, ordinary["import_id"]) == frozen
    assert_effective(database, cid, 1, "1.00")
    assert coverage(client, cid)["unresolved_records"] == 0


@pytest.mark.parametrize("patch", [
    {"摘要说明": "退款冲正"}, {"交易金额": "-9.00"}, {"交易余额": "99.00"},
    {"收付标志": "入"}, {"交易币种": "USD"}, {"交易流水号": "SYN-UNMATCHED"},
    {"交易户名": "Synthetic other owner"},
], ids=["refund", "amount", "balance", "direction", "currency", "reference", "identity"])
def test_unclosed_negative_never_acquires_a_cross_container_exclusion(
        client, database, make_case, patch):
    cid = make_case("Synthetic unclosed cancellation container")
    rows = round_trip()
    rows[2].update(patch)
    payload, content = source(rows, path="bank/renamed.csv")
    ordinary, _ = ingest(client, cid, payload)
    before, frozen = raw(database, cid), source_snapshot(database, cid, ordinary["import_id"])
    archive, job = ingest(client, cid, content, "synthetic.zip")
    assert job["result"]["table_summary"]["cancelled"] == 0
    assert source_records(client, cid, archive["import_id"])[2]["disposition"] == "unresolved"
    assert raw(database, cid) == before
    assert source_snapshot(database, cid, ordinary["import_id"]) == frozen
    assert_effective(database, cid, 2, "11.00")
    assert_gap(client, cid)


def test_removing_bom_keeps_a_valid_csv_but_does_not_share_the_other_bytes_proof(client, database, make_case):
    cid = make_case("Synthetic different bytes cancellation scope")
    payload, content = source(round_trip(), path="bank/renamed.csv")
    changed = payload.removeprefix(b"\xef\xbb\xbf")
    assert changed != payload
    ordinary, first = ingest(client, cid, changed)
    assert first["result"]["ok"] == 2
    before = raw(database, cid)
    _, archive = ingest(client, cid, content, "synthetic.zip")
    assert archive["result"]["table_summary"]["cancelled"] == 2
    assert archive["result"]["ok"] == 1
    effective_ids = {row["id"] for row in database.q_raw("select id from effective_flows where case_id=?", [cid])}
    assert {row["id"] for row in before} <= effective_ids
    assert_effective(database, cid, 3, "12.00")
    assert source_records(client, cid, ordinary["import_id"])[2]["reason_code"] == "negative_amount"
    assert_gap(client, cid)


def test_cancellation_proof_does_not_cross_case_boundaries(client, database, make_case):
    cid, other = make_case("Synthetic ordinary cancellation case"), make_case("Synthetic separate proof case")
    payload, content = source(round_trip(), path="bank/renamed.csv")
    ordinary, _ = ingest(client, cid, payload)
    proof, _ = ingest(client, other, content, "synthetic.zip")
    assert_effective(database, cid, 2, "11.00")
    assert_effective(database, other, 1, "1.00")
    assert_gap(client, cid)
    assert coverage(client, other)["unresolved_records"] == 0
    assert client.get(f"/api/v1/cases/{other}/sources/{ordinary['import_id']}/report").status_code == 404
    assert client.get(f"/api/v1/cases/{cid}/sources/{proof['import_id']}/report").status_code == 404


def test_excel_transactions_do_not_become_ordinary_csv_links(client, database, make_case):
    from openpyxl import Workbook

    cid = make_case("Synthetic Excel is not a text CSV proof target")
    rows = round_trip()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(COLUMNS)
    for row in rows:
        sheet.append([row[column] for column in COLUMNS])
    stream = io.BytesIO()
    workbook.save(stream)
    ordinary, _ = ingest(client, cid, stream.getvalue(), "synthetic.xlsx")
    before = raw(database, cid)
    _, content = source(rows, path="bank/renamed.csv")
    _, proof = ingest(client, cid, content, "synthetic.zip")
    assert proof["result"]["table_summary"]["cancelled"] == 2
    effective_ids = {row["id"] for row in database.q_raw("select id from effective_flows where case_id=?", [cid])}
    assert {row["id"] for row in before} <= effective_ids
    assert source_records(client, cid, ordinary["import_id"])[2]["reason_code"] == "negative_amount"
    assert_gap(client, cid)


@pytest.mark.parametrize("kind", ["excel-metadata", "unknown-profile", "non-table-kind"])
def test_synthetic_legacy_metadata_cannot_disguise_an_ineligible_source_as_ordinary_csv(
        client, database, make_case, kind):
    cid = make_case("Synthetic legacy ordinary qualification boundary")
    payload, content = source(round_trip(), path="bank/renamed.csv")
    ordinary, _ = ingest(client, cid, payload)
    # These are controlled durable legacy metadata variants of a public import,
    # not claims that the current parser emits a mislabelled source.
    if kind == "excel-metadata":
        database.x("update import_sources set filename='legacy.xlsx',sheet='Workbook' where source_id=?", [ordinary["import_id"]])
    elif kind == "unknown-profile":
        database.x("update import_sources set parser_version='funds-trace-table-v99' where source_id=?", [ordinary["import_id"]])
    else:
        database.x("update import_sources set row_kind='csv_record' where source_id=?", [ordinary["import_id"]])
    original = next(row for row in raw(database, cid) if row["amount"] == Decimal("10.00"))
    _, proof = ingest(client, cid, content, "synthetic.zip")
    assert proof["result"]["table_summary"]["cancelled"] == 2
    assert database.q_raw("select id from effective_flows where case_id=? and id=?", [cid, original["id"]]) == [{"id": original["id"]}]


def test_another_physical_row_in_the_same_bytes_remains_an_effective_payment(client, database, make_case):
    cid = make_case("Synthetic distinct row is not cancelled by another pair")
    rows = round_trip() + [transaction(**{
        "交易时间": "2026-01-01 10:03:00", "交易金额": "10.00", "交易余额": "90.00", "交易流水号": "SYN-UNCANCELLED"})]
    payload, content = source(rows, path="bank/renamed.csv")
    ordinary, _ = ingest(client, cid, payload)
    records = source_records(client, cid, ordinary["import_id"])
    assert [record["source_row"] for record in records] == [2, 3, 4, 5]
    retained_id = int(records[3]["flow_id"])
    _, proof = ingest(client, cid, content, "synthetic.zip")
    assert proof["result"]["table_summary"]["cancelled"] == 2
    assert database.q_raw("select id from effective_flows where case_id=? and id=?", [cid, retained_id]) == [{"id": retained_id}]
    assert_effective(database, cid, 2, "11.00")
    assert coverage(client, cid)["unresolved_records"] == 0


@pytest.mark.parametrize("extra", ["different-bytes", "missing-journal", "other-archive-path"])
def test_one_unproved_source_link_keeps_the_shared_raw_flow_effective(
        client, database, make_case, monkeypatch, extra):
    cid = make_case("Synthetic every source link needs its own proof")
    payload, content = source(round_trip(), path="bank/renamed.csv")
    ordinary, _ = ingest(client, cid, payload)
    if extra == "other-archive-path":
        other = archive_bytes([("bank/other.csv", payload)])
        additional = frozen_preview(client, cid, other, monkeypatch, "funds-trace-table-archive-v2")
        added = done(client, cid, additional)
    else:
        changed = payload.removeprefix(b"\xef\xbb\xbf")
        assert changed != payload
        additional, added = ingest(client, cid, changed, "extra.csv")
        if extra == "missing-journal":
            # A real legacy flow_source may predate the table journals. Its
            # presence must count even though it cannot supply a new proof.
            database.x("delete from import_records where case_id=? and source_id=?", [cid, additional["import_id"]])
            database.x("delete from import_members where case_id=? and source_id=?", [cid, additional["import_id"]])
            database.x("update import_sources set row_kind='csv_record',parser_version='funds-trace-table-v4' where source_id=?",
                       [additional["import_id"]])
    assert (added["result"]["ok"], added["result"]["rows_dup"]) == (0, 2)
    before = raw(database, cid)
    original = next(row for row in before if row["amount"] == Decimal("10.00"))
    frozen = source_snapshot(database, cid, ordinary["import_id"])
    _, proof = ingest(client, cid, content, "current.zip")
    assert proof["result"]["table_summary"]["cancelled"] == 2
    assert raw(database, cid) == before
    assert source_snapshot(database, cid, ordinary["import_id"]) == frozen
    assert database.q_raw("select id from effective_flows where case_id=? and id=?", [cid, original["id"]]) == [{"id": original["id"]}]
    assert_effective(database, cid, 2, "11.00")
    if extra != "missing-journal":
        assert_gap(client, cid)


@pytest.mark.parametrize("diagnosis", ["compound", "role", "history", "archive-history", "financial", "future"])
def test_shared_proof_does_not_clear_other_durable_diagnostics(
        client, database, make_case, diagnosis):
    cid = make_case("Synthetic cancellation coverage diagnostic boundary")
    payload, content = source(round_trip(), path="bank/renamed.csv")
    ordinary, first = ingest(client, cid, payload)
    _, proof = ingest(client, cid, content, "synthetic.zip")
    assert proof["result"]["table_summary"]["cancelled"] == 2
    assert coverage(client, cid)["unresolved_records"] == 0
    negative = database.q_raw("select details_json from import_records where source_id=? and record_no=3", [ordinary["import_id"]])[0]
    details = json.loads(negative["details_json"])
    # Simulate an additional durable historical assessment, preserving the
    # public source, actual flows, physical row, and all proof journals. This
    # tests the read-time boundary rather than inventing current bank rules.
    reason = "negative_amount"
    if diagnosis == "compound":
        details["validation_issues"] = "negative_amount,missing_time"
    elif diagnosis == "future":
        database.x("update import_sources set parser_version='funds-trace-table-v99' where source_id=?", [ordinary["import_id"]])
    else:
        reason = "conflicting_field"
        details["validation_issues"] = "conflicting_field"
        if diagnosis == "financial":
            details["validation_basis"] = table_import.FINANCIAL_CONFLICT_BASIS
        else:
            details["counterparty_basis"] = {
                "role": table_import.ROLE_CONFLICT_BASIS,
                "history": table_import.HISTORY_CONFLICT_BASIS,
                "archive-history": table_import.ARCHIVE_HISTORY_CONFLICT_BASIS,
            }[diagnosis]
    database.x("update import_records set reason_code=?,details_json=? where source_id=? and record_no=3",
               [reason, json.dumps(details, ensure_ascii=False), ordinary["import_id"]])
    assert_gap(client, cid)
    record = source_records(client, cid, ordinary["import_id"])[2]
    assert (record["disposition"], record["reason_code"], record["flow_id"]) == ("unresolved", reason, None)
    assert receipt(client, cid, ordinary["import_id"]) == first["result"]
    if diagnosis in {"role", "history", "archive-history", "financial", "future"}:
        assert_effective(database, cid, 2, "11.00")


@pytest.mark.parametrize("change", ["matching", "original", "witness"])
def test_late_v5_ready_checks_current_proof_before_storing_a_competing_interpretation(
        client, database, make_case, monkeypatch, change):
    cid = make_case("Synthetic late ready shares only matching original facts")
    rows = round_trip()
    for row in rows:
        row["IP地址"] = row["摘要说明"]
    index = {"original": 1, "witness": 0}.get(change)
    if index is not None:
        rows[index]["IP地址"] = "Synthetic alternate summary"
    payload, content = source(rows, path="bank/renamed.csv")
    old = summary_ready(client, cid, payload, monkeypatch, "funds-trace-table-v5")
    assert old["table_summary"]["accepted"] == 2
    proof, proof_job = ingest(client, cid, content, "current.zip")
    frozen = source_snapshot(database, cid, proof["import_id"])
    old = repreview(client, cid, old)
    late = done(client, cid, old)
    records = source_records(client, cid, old["import_id"])
    assert source_snapshot(database, cid, proof["import_id"]) == frozen
    assert receipt(client, cid, proof["import_id"]) == proof_job["result"]
    assert len(raw(database, cid)) == (1 if change == "original" else 2)
    if change == "matching":
        assert (late["result"]["ok"], late["result"]["rows_dup"]) == (1, 1)
        assert records[1]["disposition"] == "imported"
        assert_effective(database, cid, 1, "1.00")
        assert coverage(client, cid)["unresolved_records"] == 0
    else:
        assert late["result"]["ok"] == (0 if change == "original" else 1)
        assert (records[index]["disposition"], records[index]["reason_code"], records[index]["flow_id"]) == (
            "unresolved", "conflicting_field", None)
        assert records[index]["details"]["counterparty_basis"] == table_import.ARCHIVE_HISTORY_CONFLICT_BASIS
        assert_gap(client, cid)
        assert_effective(database, cid, 1 if change == "original" else 2, "1.00" if change == "original" else "11.00")


@pytest.mark.parametrize("leg", ["original", "negative", "witness"])
def test_first_pair_historical_conflict_blocks_its_dependent_chain_but_not_the_independent_pair(
        client, database, make_case, monkeypatch, leg):
    cid = make_case("Synthetic selective cross container cancellation chain")
    rows = chain_rows()
    for row in rows:
        row["IP地址"] = row["摘要说明"]
    if leg != "negative":
        rows[0 if leg == "witness" else 1]["IP地址"] = "Synthetic historical alternate summary"
    payload, content = source(rows, path="bank/renamed.csv")
    profile = "funds-trace-table-v5" if leg == "negative" else "funds-trace-table-v17"
    ordinary = summary_ready(client, cid, payload, monkeypatch, profile)
    old_job = done(client, cid, ordinary)
    if leg == "negative":
        historical_positive_negative_leg(database, cid, ordinary)
        # The controlled v5 legacy state must itself pass the unchanged strict
        # package boundary before it is used as conflicting historical input.
        restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
        assert facts(database, restored) == facts(database, cid)
    before, frozen = raw(database, cid), source_snapshot(database, cid, ordinary["import_id"])
    proof, job = ingest(client, cid, content, "current.zip")
    assert job["result"]["ok"] == 0
    assert job["result"]["table_summary"]["cancelled"] == 2
    records = source_records(client, cid, proof["import_id"])
    assert all(record["disposition"] == "unresolved" for record in records[1:5])
    assert [record["disposition"] for record in records[5:]] == ["duplicate", "cancelled", "cancelled"]
    assert raw(database, cid) == before
    assert source_snapshot(database, cid, ordinary["import_id"]) == frozen
    assert receipt(client, cid, ordinary["import_id"]) == old_job["result"]
    assert_effective(database, cid, len(before) - 1, "42.00" if leg == "negative" else "32.00")
    assert_gap(client, cid)


def test_late_legacy_positive_negative_leg_revokes_shared_chain_without_rewriting_the_proof(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic late legacy positive cancellation leg")
    payload, content = source(chain_rows(), path="bank/renamed.csv")
    proof, proof_job = ingest(client, cid, content, "current.zip")
    ordinary = profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v5")
    old_job = done(client, cid, ordinary)
    assert_effective(database, cid, 2, "2.00")
    frozen = source_snapshot(database, cid, proof["import_id"])
    historical_positive_negative_leg(database, cid, ordinary)
    before = raw(database, cid)
    assert len(before) == 6
    assert_effective(database, cid, 5, "42.00")
    value = assert_gap(client, cid)
    assert source_snapshot(database, cid, proof["import_id"]) == frozen
    assert receipt(client, cid, proof["import_id"]) == proof_job["result"]
    assert receipt(client, cid, ordinary["import_id"]) == old_job["result"]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == facts(database, cid)
    assert_effective(database, restored, 5, "42.00")
    assert coverage(client, restored) == value
    assert raw(database, cid) == before


@pytest.mark.parametrize("field", ["self_account", "self_name"])
def test_actual_historical_self_identity_is_compared_even_when_full_money_digest_matches(
        client, database, make_case, monkeypatch, field):
    cid = make_case("Synthetic actual own identity cancellation binding")
    payload, content = source(round_trip(), path="bank/renamed.csv")
    ordinary = profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v5")
    first = done(client, cid, ordinary)
    original = next(row for row in raw(database, cid) if row["amount"] == Decimal("10.00"))
    # A controlled old v5 fact has a different own-side interpretation. The
    # monetary digest deliberately cannot prove either self identity field.
    digest = table_import.flow_digest(original)
    database.x(f"update flows set {field}=? where case_id=? and id=?", ["SYN-LEGACY-OTHER", cid, original["id"]])
    changed = database.q_raw("select * from flows where case_id=? and id=?", [cid, original["id"]])[0]
    assert table_import.flow_digest(changed) == digest
    before, frozen = raw(database, cid), source_snapshot(database, cid, ordinary["import_id"])
    proof, job = ingest(client, cid, content, "current.zip")
    assert job["result"]["table_summary"]["cancelled"] == 0
    assert source_records(client, cid, proof["import_id"])[1]["reason_code"] == "cancellation_historical_conflict"
    assert raw(database, cid) == before
    assert source_snapshot(database, cid, ordinary["import_id"]) == frozen
    assert receipt(client, cid, ordinary["import_id"]) == first["result"]
    assert_effective(database, cid, 2, "11.00")
    assert_gap(client, cid)


def test_commit_rechecks_a_competing_ordinary_interpretation_added_after_pair_preview(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic cancellation proof preview race")
    rows = round_trip()
    for row in rows:
        row["IP地址"] = row["摘要说明"]
    rows[1]["IP地址"] = "Synthetic later original summary"
    payload, content = source(rows, path="bank/renamed.csv")
    ready = preview(client, cid, content, "synthetic.zip")
    assert ready["table_summary"]["cancelled"] == 2
    ordinary = summary_ready(client, cid, payload, monkeypatch)
    done(client, cid, ordinary)
    before, frozen = raw(database, cid), source_snapshot(database, cid, ordinary["import_id"])
    committed = done(client, cid, ready)
    assert committed["result"]["table_summary"]["cancelled"] == 0
    assert committed["result"]["table_summary"]["reason_counts"]["cancellation_historical_conflict"] == 2
    assert raw(database, cid) == before
    assert source_snapshot(database, cid, ordinary["import_id"]) == frozen
    assert_effective(database, cid, 2, "11.00")
    assert_gap(client, cid)


def test_failed_proof_journal_rolls_back_and_reopening_does_not_replay_it(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic cross container proof atomic rollback")
    payload, content = source(round_trip(), path="bank/renamed.csv")
    ordinary, old_job = ingest(client, cid, payload)
    before, old_gap = persisted(database), assert_gap(client, cid)
    proof = preview(client, cid, content, "synthetic.zip")
    write = database.insert_dataframe

    def fail_after_proof_journal(table, frame, columns):
        result = write(table, frame, columns)
        if table == "import_records":
            raise RuntimeError("Synthetic A02 failure after proof journal write")
        return result

    with monkeypatch.context() as failure:
        failure.setattr(database, "insert_dataframe", fail_after_proof_journal)
        failed = complete(client, cid, proof)
    assert failed["stage"] == "error" and failed["result"] is None
    assert persisted(database) == before
    with database._lock:
        database._con.close()
        database._con = None
    imports.recover_interrupted()
    assert persisted(database) == before
    assert coverage(client, cid) == old_gap
    assert_effective(database, cid, 2, "11.00")
    assert receipt(client, cid, ordinary["import_id"]) == old_job["result"]
    retried = done(client, cid, proof)
    assert (retried["result"]["ok"], retried["result"]["rows_dup"]) == (0, 1)
    assert_effective(database, cid, 1, "1.00")
    assert coverage(client, cid)["unresolved_records"] == 0


RESTORE_CHILD = r'''
import base64,json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from app import db
db._start_ckpt=lambda:None
from app.main import app
from fastapi.testclient import TestClient
from test_table_cancellation_container import ingest,raw,effective
from test_table_currency_region_lifecycle import source_snapshot
from test_table_mapping_coverage import coverage
control=json.load(sys.stdin)
results=[]
with TestClient(app) as client:
    assert db.q_raw('select * from flows')==[]
    for key in ['before','after']:
        response=client.post('/api/cases/import',files={'file':('synthetic.fundcase',base64.b64decode(control[key]))})
        assert response.status_code==200,response.text
        cid=response.json()['case_id']
        assert db.q_raw('select * from import_sessions where case_id=?',[cid])==[]
        initial=raw(db,cid)
        assert len(initial)==5
        frozen={row['source_id']:source_snapshot(db,cid,row['source_id'])
                for row in db.q_raw('select source_id from import_sources where case_id=?',[cid])}
        if key=='before':
            assert effective(db,cid)[0]['amount']==42
            assert coverage(client,cid)['unresolved_records']==3
        else:
            assert effective(db,cid)[0]['amount']==2
            assert coverage(client,cid)['unresolved_records']==0
        for data,filename in [(control['archive'],'restored.zip'),(control['csv'],'repeat.csv'),(control['archive'],'repeat.zip')]:
            _,job=ingest(client,cid,base64.b64decode(data),filename)
            assert job['result']['ok']==0
        assert raw(db,cid)==initial
        for source_id,snapshot in frozen.items():
            assert source_snapshot(db,cid,source_id)==snapshot
        assert effective(db,cid)==[{'currency':'CNY','n':2,'amount':2}]
        stats=coverage(client,cid)
        assert stats['unresolved_records']==0 and stats['cancelled_records']==6
        assert stats['financial_interpretation_complete'] is True
        results.append({'raw':len(initial),'effective':2,'unresolved':0})
print('A02_RESULT='+json.dumps({'database':str(db.DB_PATH),'cases':results}))
db._con.close()
db._con=None
'''


def test_two_pair_chain_and_independent_pair_restore_in_a_clean_process_without_replaying_money(
        client, database, make_case, tmp_path):
    cid = make_case("Synthetic portable cross container cancellation chain")
    payload, content = source(chain_rows(), path="bank/renamed.csv")
    ordinary, old_job = ingest(client, cid, payload)
    before_package = case_packages.export_package(cid)[0]
    before = raw(database, cid)
    assert len(before) == 5
    assert_effective(database, cid, 5, "42.00")
    proof, job = ingest(client, cid, content, "synthetic.zip")
    assert (job["result"]["ok"], job["result"]["table_summary"]["cancelled"]) == (0, 6)
    assert raw(database, cid) == before
    assert receipt(client, cid, ordinary["import_id"]) == old_job["result"]
    assert_effective(database, cid, 2, "2.00")
    assert coverage(client, cid)["unresolved_records"] == 0
    assert [record["disposition"] for record in source_records(client, cid, proof["import_id"])] == [
        "duplicate", "cancelled", "cancelled", "cancelled", "cancelled", "duplicate", "cancelled", "cancelled"]
    after_package = case_packages.export_package(cid)[0]
    unchanged = persisted(database)
    runtime = tmp_path / "clean-a02-child"
    paths = {key: runtime / name for key, name in (
        ("FUND_DATA_DIR", "data"), ("MPLCONFIGDIR", "mpl"), ("XDG_CACHE_HOME", "cache"),
        ("TMPDIR", "tmp"), ("TMP", "tmp"), ("TEMP", "tmp"))}
    for directory in set(paths.values()):
        directory.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **{key: str(path) for key, path in paths.items()},
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1",
           "FUNDS_TRACE_LICENSE_SECRET": "synthetic-regression-key-not-for-use"}
    env.pop("FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE", None)
    encoded = lambda data: base64.b64encode(data).decode("ascii")
    control = {"before": encoded(before_package), "after": encoded(after_package),
               "csv": encoded(payload), "archive": encoded(content)}
    child = subprocess.run([sys.executable, "-c", RESTORE_CHILD], input=json.dumps(control),
                           cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True,
                           text=True, encoding="utf-8", timeout=45)
    assert child.returncode == 0, child.stdout + child.stderr
    result = json.loads(next(line.removeprefix("A02_RESULT=") for line in child.stdout.splitlines()
                             if line.startswith("A02_RESULT=")))
    assert Path(result["database"]) == paths["FUND_DATA_DIR"] / "fund.duckdb"
    assert result["cases"] == [{"raw": 5, "effective": 2, "unresolved": 0}] * 2
    assert persisted(database) == unchanged
