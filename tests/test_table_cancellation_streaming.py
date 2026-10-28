"""Synthetic cancellation replay keeps the complete parser's exact facts."""
import copy
import csv
import io
import json

import pytest

from app import feedback_import, table_archive, table_cancellations, table_import
from test_folder_evidence import accepted, archive
from test_table_cancellations import COLUMNS, round_trip


FORMATS = [("utf-8-sig", ","), ("utf-16", "\t"), ("gb18030", ";"), ("utf-8-sig", "|")]


def payload(*, columns=COLUMNS, encoding="utf-8-sig", delimiter=",", reordered=False,
            rows=None, tail=()):
    rows = copy.deepcopy(round_trip() if rows is None else rows)
    for row in rows:
        row["备注"] = '合成说明, "quoted"\nsecond line'
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=delimiter, quoting=csv.QUOTE_ALL)
    writer.writerow([" \t" + column + "\t " for column in columns])
    writer.writerow([rows[0].get(column, "") for column in columns])
    if reordered:
        columns = tuple(reversed(columns))
        writer.writerow(columns)
    writer.writerows([row.get(column, "") for column in columns] for row in rows[1:])
    writer.writerows(tail)
    return stream.getvalue().encode(encoding)


def primary(content):
    return table_import.parse(content, suffix=".csv", profile=table_import.EVIDENCE_ARCHIVE_PROFILE,
                              reference_policy=table_import.TRANSACTION_REFERENCE_POLICY, _retain_csv_format=True)


def compare_paths(content):
    original = primary(content)
    legacy, streamed = copy.deepcopy(original), copy.deepcopy(original)
    table_cancellations.apply(legacy, content, allow_feedback28=True)
    table_cancellations.apply(streamed, content, allow_feedback28=True, csv_format=original["_csv_format"])
    # Compare every fact, explanation, source position, mapping and summary.
    assert streamed == legacy
    return streamed


@pytest.mark.parametrize("encoding,delimiter", FORMATS)
@pytest.mark.parametrize("columns", [COLUMNS, feedback_import.COLUMNS], ids=["closed32", "closed28"])
@pytest.mark.parametrize("reordered", [False, True], ids=["one-header", "reordered-header"])
def test_streamed_and_materialized_replay_are_field_for_field_equal(encoding, delimiter, columns, reordered):
    plan = compare_paths(payload(columns=columns, encoding=encoding, delimiter=delimiter, reordered=reordered))
    assert plan["_csv_format"] == {"encoding": encoding, "delimiter": delimiter}
    assert plan["cancellations"] == [{"original_record_no": 3 if reordered else 2,
                                       "cancellation_record_no": 4 if reordered else 3,
                                       "witness_record_no": 1}]
    assert plan["stats"]["cancelled"] == 2 and plan["stats"]["accepted"] == 1
    assert plan["stats"]["currency_totals"] == {"CNY": "1.00"}
    assert [record["source_row"] for record in plan["records"]] == list(range(2, 6 if reordered else 5))
    assert table_cancellations.validate_evidence(plan["records"]) == plan["cancellations"]


@pytest.mark.parametrize("kind", ["bad-width", "mixed-header", "third-reference", "equal-time", "missing-scope"])
def test_streamed_replay_preserves_whole_member_rejection(kind):
    rows, tail = round_trip(), ()
    if kind == "bad-width":
        tail = [("unexpected",)]
    elif kind == "mixed-header":
        tail = [("交易账号", "交易时间", "交易金额")]
    elif kind == "third-reference":
        rows.append(rows[1] | {"交易时间": "2026-01-01 10:03:00"})
    elif kind == "equal-time":
        rows.append(rows[0] | {"交易时间": rows[1]["交易时间"], "交易流水号": "SYN-EXTRA"})
    else:
        rows[1]["交易账号"] = ""
    plan = compare_paths(payload(rows=rows, tail=tail))
    assert plan["cancellations"] == [] and plan["stats"]["cancelled"] == 0
    assert plan["records"][2]["disposition"] == "unresolved"


def test_archive_uses_one_complete_csv_parse_and_keeps_format_private(monkeypatch):
    content = payload(encoding="gb18030", delimiter=";", reordered=True)
    original, calls = table_import._csv, []

    def counted(*args, **kwargs):
        calls.append(len(args[0]))
        return original(*args, **kwargs)

    monkeypatch.setattr(table_import, "_csv", counted)
    plan = table_archive.parse(io.BytesIO(archive([("synthetic.csv", content)])), profile=table_archive.EVIDENCE_PROFILE)
    try:
        assert calls == [len(content)]
        assert plan["stats"]["cancelled"] == 2
        assert "_csv_format" not in plan
        for key in ("members", "documents", "mapping_evidence"):
            assert "_csv_format" not in json.dumps(plan[key])
        assert all("_csv_format" not in record["details"] for record in plan["records"])
    finally:
        plan["_details_pool"].close()


@pytest.mark.parametrize("preamble", [False, True])
def test_verified_ineligible_document_skips_second_reader(monkeypatch, preamble):
    content = ("合成说明\r\n" if preamble else "") + "交易时间,付款账号,收款账号,交易金额,币种\r\n2026-01-01,SYN-A,SYN-B,1.00,CNY\r\n"
    content = content.encode("utf-8-sig")
    expected = primary(content)
    actual = copy.deepcopy(expected)
    table_cancellations.apply(expected, content, allow_feedback28=True)

    def forbidden(*args, **kwargs):
        raise AssertionError("verified ineligible document must not re-read CSV")

    monkeypatch.setattr(table_cancellations, "_source_records", forbidden)
    table_cancellations.apply(actual, content, allow_feedback28=True, csv_format=actual["_csv_format"])
    assert actual == expected


@pytest.mark.parametrize("selected", [None, {}, {"encoding": "utf-8", "delimiter": ","},
                                       {"encoding": "utf-8-sig", "delimiter": ",", "extra": True}])
def test_missing_or_invalid_format_retains_complete_detection(monkeypatch, selected):
    content = payload()
    expected = compare_paths(content)
    actual = primary(content)
    original, calls = table_import._csv, []

    def counted(*args, **kwargs):
        calls.append(len(args[0]))
        return original(*args, **kwargs)

    monkeypatch.setattr(table_import, "_csv", counted)
    table_cancellations.apply(actual, content, allow_feedback28=True, csv_format=selected)
    assert calls == [len(content)] and actual == expected


@pytest.mark.parametrize("tail", [b'"unterminated', b'\xff'])
@pytest.mark.parametrize("eligible", [False, True])
def test_stream_reader_checks_bad_tail_even_when_first_header_is_ineligible(tail, eligible):
    content = payload() if eligible else b"unknown,columns\r\nsynthetic,values\r\n"
    plan = primary(content)
    # No document fast path: this exercises the strict stream independently.
    plan["documents"] = []
    with pytest.raises(table_import.TableImportError, match="CSV"):
        table_cancellations.apply(plan, content + tail, allow_feedback28=True, csv_format=plan["_csv_format"])


def test_public_directory_round_trip_keeps_logical_rows_and_cancellation_money(client, database, make_case):
    cid = make_case("Synthetic streaming cancellation")
    content = payload(encoding="utf-16", delimiter=",", reordered=True)
    view, receipt = accepted(client, cid, [("synthetic.csv", content)], scope_bank=None)
    assert view["table_summary"]["cancelled"] == receipt["table_summary"]["cancelled"] == 2
    assert receipt["ok"] == 1
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()
    assert flows["total"] == 1 and flows["items"][0]["amount"] == "1.00"
    response = client.get(f"/api/v1/cases/{cid}/sources/{receipt['source_id']}/report")
    assert response.status_code == 200, response.text
    report = response.json()
    assert [record["disposition"] for record in report["items"]] == ["imported", "non_transaction", "cancelled", "cancelled"]
    assert [str(record["source_row"]) for record in report["items"]] == ["2", "3", "4", "5"]
    for value in (view, receipt, report):
        assert "_csv_format" not in json.dumps(value) and "_details_pool" not in json.dumps(value)


@pytest.mark.parametrize("tail", [b'"unterminated', b'\xff'])
@pytest.mark.parametrize("eligible", [False, True])
def test_public_bad_tail_is_rejected_before_any_cancellation_fast_path(client, database, make_case, tail, eligible):
    cid = make_case("Synthetic invalid streaming source")
    content = payload() if eligible else b"transaction_time,payer_account,payee_account,amount,currency\r\n2026-01-01,SYN-A,SYN-B,1.00,CNY\r\n"
    response = client.post(f"/api/v1/cases/{cid}/imports/directory/preview",
                           files=[("files", ("synthetic.csv", content + tail, "text/csv"))])
    assert response.status_code == 400, response.text
    for table in ("flows", "batches", "import_sources", "flow_sources", "import_records"):
        assert database.q_raw(f"select count(*) n from {table} where case_id=?", [cid])[0]["n"] == 0
