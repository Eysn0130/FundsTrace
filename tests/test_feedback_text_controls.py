"""Synthetic v4 transport controls preserve the source without relaxing facts."""
import csv
import hashlib
import io
import json
import zipfile

from fastapi import HTTPException
import pytest

from app import case_packages, feedback_import as feedback
from test_cancellation_import_lifecycle import count, import_version, paired_source
from test_feedback_import_api import archive, complete, mixed_rows, preview, transaction
from test_feedback_package import _archive, _members, _mutate


V4 = "funds-trace-bank-feedback-v4"
FS = "\x1c"
LOCATION = FS + 'Synthetic, "location"\nsecond line' + FS * 4


@pytest.fixture(autouse=True)
def _historical_v4_default(monkeypatch):
    monkeypatch.setattr(feedback, "PROFILE", feedback.TEXT_PROFILE)


def _source(*, column="交易发生地", value=LOCATION, role="transaction", encoding="gb18030", header=False):
    columns = list(feedback.SCHEMAS[role])
    row = transaction() if role == "transaction" else dict.fromkeys(columns, "")
    if header:
        columns[columns.index(column)] += value
    else:
        row[column] = value
    text = io.StringIO(newline="")
    writer = csv.writer(text, quoting=csv.QUOTE_ALL)
    writer.writerow(columns)
    writer.writerow([row.get(column, "") for column in columns])
    return _archive({"synthetic/records.csv": text.getvalue().encode(encoding)})


def _reject(content, profile, code="binary_member"):
    with pytest.raises(feedback.FeedbackImportError) as caught:
        feedback.parse(io.BytesIO(content), profile=profile)
    assert caught.value.code == code


@pytest.mark.parametrize("encoding", ["utf-8-sig", "gb18030"])
def test_v4_location_control_is_preserved_with_csv_position_and_original_hash(encoding):
    content = _source(encoding=encoding)
    for profile in (feedback.IDENTITY_PROFILE, feedback.PREVIOUS_PROFILE, feedback.CANCELLATION_PROFILE):
        _reject(content, profile)
    plan = feedback.parse(io.BytesIO(content), profile=V4)
    assert feedback.PROFILE == V4
    assert plan["profile"] == V4 and plan["stats"]["ok"] == 1
    assert plan["stats"]["cancelled"] == 0
    assert plan["records"][0]["source_row"] == 2
    assert plan["records"][0]["member_path"] == "synthetic/records.csv"
    payload = _members(content)["synthetic/records.csv"]
    assert plan["members"][0]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert plan["members"][0]["row_count"] == 1
    text, codec = feedback._decode(payload, profile=V4)
    table = feedback._table(text, codec, profile=V4)
    assert next(table)[0] == "transaction"
    assert next(table)["交易发生地"] == LOCATION
    assert payload.count(b"\x1c") == 5


@pytest.mark.parametrize("profile", [feedback.IDENTITY_PROFILE, feedback.PREVIOUS_PROFILE, feedback.CANCELLATION_PROFILE])
def test_historical_default_cannot_enable_the_v4_transport_rule(monkeypatch, profile):
    monkeypatch.setattr(feedback, "PROFILE", profile)
    _reject(_source(), profile)


@pytest.mark.parametrize("kind", ["paired", "mixed"])
def test_v4_keeps_v3_financial_identity_and_provenance_semantics(kind):
    content = paired_source() if kind == "paired" else archive(mixed_rows())
    old = feedback.parse(io.BytesIO(content), profile=feedback.CANCELLATION_PROFILE)
    current = feedback.parse(io.BytesIO(content), profile=V4)
    assert old.pop("profile") == feedback.CANCELLATION_PROFILE
    assert current.pop("profile") == V4
    assert current == old


@pytest.mark.parametrize("column", [column for column in feedback.COLUMNS if column != "交易发生地"])
def test_v4_rejects_file_separator_in_every_other_transaction_column(column):
    _reject(_source(column=column, value=FS), V4)


@pytest.mark.parametrize("role", [role for role in feedback.SCHEMAS if role != "transaction"])
def test_v4_rejects_file_separator_in_every_auxiliary_role(role):
    _reject(_source(role=role, column=feedback.SCHEMAS[role][-1], value=FS), V4)


@pytest.mark.parametrize("header", ["交易发生地", "交易账号"])
@pytest.mark.parametrize("value", [FS, FS + "x"])
def test_v4_rejects_header_controls_before_stripping(header, value):
    _reject(_source(column=header, value=value, header=True), V4)


@pytest.mark.parametrize("control", [chr(number) for number in range(32) if chr(number) not in "\t\r\n\x1c"])
def test_v4_rejects_other_binary_controls_even_in_location(control):
    _reject(_source(value="Synthetic" + control), V4)


@pytest.mark.parametrize("malformation,code", [("quote", "invalid_csv"), ("width", "csv_width"), ("path", "unsafe_member_path")])
def test_v4_location_allowlist_does_not_relax_csv_or_archive_structure(malformation, code):
    members = _members(_source())
    path = "synthetic/records.csv"
    if malformation == "quote":
        members[path] = members[path].replace(b'"SYN-T1"', b'SYN-"T1')
    elif malformation == "width":
        members[path] += b'"short"\r\n'
    else:
        members["../records.csv"] = members.pop(path)
    _reject(_archive(members), V4, code)


def _numbered_source(records, *, prefix="SYN-RESOURCE"):
    return archive([transaction(**{"交易流水号": f"{prefix}-{number}"}) for number in range(records)], account=False)


@pytest.mark.parametrize("profile", [feedback.IDENTITY_PROFILE, feedback.PREVIOUS_PROFILE, feedback.CANCELLATION_PROFILE])
def test_v4_record_resource_cap_preserves_historical_limit_without_truncation(monkeypatch, profile):
    monkeypatch.setattr(feedback, "MAX_RECORDS", 2)
    monkeypatch.setattr(feedback, "TEXT_MAX_RECORDS", 4)
    monkeypatch.setattr(feedback, "PROFILE", profile)
    assert len(feedback.parse(io.BytesIO(_numbered_source(2)), profile=profile)["records"]) == 2
    _reject(_numbered_source(3), profile, "record_count")
    plan = feedback.parse(io.BytesIO(_numbered_source(4)), profile=V4)
    assert len(plan["records"]) == len(plan["rows"]) == plan["stats"]["record_total"] == 4
    assert [record["source_row"] for record in plan["records"]] == [2, 3, 4, 5]
    assert [row["trade_no"] for row in plan["rows"]] == [f"SYN-RESOURCE-{number}" for number in range(4)]


def test_v4_record_resource_cap_rejects_whole_upload_over_all_members(client, database, make_case, monkeypatch):
    monkeypatch.setattr(feedback, "MAX_RECORDS", 2)
    monkeypatch.setattr(feedback, "TEXT_MAX_RECORDS", 4)
    members = _members(_numbered_source(3))
    members["synthetic/other.csv"] = _members(_numbered_source(2, prefix="SYN-OTHER"))["synthetic/transactions.csv"]
    content = _archive(members)
    _reject(content, V4, "record_count")
    cid = make_case("Synthetic rejected record resource cap")
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("synthetic.zip", content)})
    assert response.status_code == 400 and "record_count" in response.text
    for table in ("flows", "batches", "flow_sources", "import_sources", "import_members", "import_records", "import_sessions"):
        assert count(database, table, cid) == 0


@pytest.mark.parametrize("limit,cap,code", [
    ("MAX_MEMBERS", 0, "member_count"), ("MAX_MEMBER_BYTES", 32, "member_size"),
    ("MAX_EXPANDED_BYTES", 32, "expanded_size"), ("MAX_FIELD_BYTES", 4, "field_size"),
])
def test_v4_record_resource_change_keeps_other_resource_limits(monkeypatch, limit, cap, code):
    monkeypatch.setattr(feedback, limit, cap)
    _reject(_source(), V4, code)


@pytest.mark.parametrize("with_table", [False, True])
def test_v4_larger_resource_cap_round_trips_mixed_versions_per_source(
        client, database, make_case, monkeypatch, tmp_path, with_table):
    monkeypatch.setattr(feedback, "MAX_RECORDS", 2)
    monkeypatch.setattr(feedback, "TEXT_MAX_RECORDS", 4)
    cid = make_case("Synthetic portable resource caps")
    _, old_job = import_version(client, cid, _numbered_source(2, prefix="SYN-OLD"), monkeypatch, feedback.CANCELLATION_PROFILE)
    _, new_job = import_version(client, cid, _numbered_source(3), monkeypatch, V4)
    assert old_job["result"]["ok"] == 2 and new_job["result"]["ok"] == 3
    if with_table:
        response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": (
            "synthetic.csv", "交易时间,交易金额,收付标志,交易账号,交易对手账卡号\n2026-01-02 10:00:00,2.00,出,SYN-C,SYN-D\n".encode())})
        assert response.status_code == 200, response.text
        assert complete(client, cid, response.json())["stage"] == "done"
    package = case_packages.export_package(cid)[0]
    members = _members(package)
    assert json.loads(members["meta.json"])["format"] == 6
    restored = case_packages.import_package(package)["case_id"]
    assert count(database, "import_records", restored) == (6 if with_table else 5)
    assert database.q_raw("""select s.parser_version,sum(m.row_count) n from import_sources s
                            join import_members m on m.source_id=s.source_id and m.case_id=s.case_id
                            where s.case_id=? and s.row_kind='archive_record' group by s.parser_version order by s.parser_version""",
                          [restored]) == [{"parser_version": feedback.CANCELLATION_PROFILE, "n": 2},
                                         {"parser_version": V4, "n": 3}]
    _mutate(members, "import_sources", "update entries set parser_version='funds-trace-bank-feedback-v3' "
            "where parser_version='funds-trace-bank-feedback-v4'", tmp_path)
    before = database.q_raw("select id from cases order by id")
    with pytest.raises(HTTPException, match="反馈包日志总量超出限制"):
        case_packages.import_package(_archive(members))
    assert database.q_raw("select id from cases order by id") == before


@pytest.mark.parametrize("column,value,header", [
    ("交易账号", FS, False), ("交易时间", FS, False), ("交易金额", FS, False),
    ("交易流水号", FS, False), ("交易发生地", FS, True), ("交易发生地", "\x00", False),
])
def test_disallowed_controls_leave_no_partial_import(client, database, make_case, column, value, header):
    cid = make_case("Synthetic rejected transport")
    result = client.post(f"/api/v1/cases/{cid}/imports/preview",
                         files={"file": ("synthetic.zip", _source(column=column, value=value, header=header))})
    assert result.status_code == 400
    assert result.json()["error"]["code"] == "invalid_feedback_archive"
    for table in ("flows", "batches", "flow_sources", "import_sources", "import_members", "import_records", "import_sessions"):
        assert count(database, table, cid) == 0


def test_v4_public_import_retains_original_bytes_and_durable_profile(client, database, make_case):
    cid = make_case("Synthetic retained control")
    content = _source()
    view = preview(client, cid, content)
    assert view["feedback"]["profile"] == V4
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    source_id = view["import_id"]
    assert client.get(f"/api/v1/cases/{cid}/sources/{source_id}/file").content == content
    journal = database.q_raw("select member_path,source_row,disposition from import_records where source_id=?", [source_id])
    assert journal == [{"member_path": "synthetic/records.csv", "source_row": 2, "disposition": "imported"}]
    assert database.q_raw("select parser_version from import_sources where source_id=?", [source_id]) == [{"parser_version": V4}]
    assert client.get(f"/api/v1/cases/{cid}/imports/{source_id}").json()["result"] == job["result"]
    history = client.get(f"/api/v1/cases/{cid}/imports").json()["items"]
    assert history[0]["result"]["feedback"]["profile"] == V4
    assert complete(client, cid, preview(client, cid, content))["result"]["rows_dup"] == 1
    assert count(database, "flows", cid) == 1


def test_ready_v3_pin_stays_strict_after_v4_default(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic frozen ready version")
    with monkeypatch.context() as old:
        old.setattr(feedback, "PROFILE", feedback.CANCELLATION_PROFILE)
        old_view = preview(client, cid, archive([transaction()]))
    assert complete(client, cid, old_view)["result"]["feedback"]["profile"] == feedback.CANCELLATION_PROFILE
    view = preview(client, cid, _source())
    # A persisted ready request must execute its recorded rule even on retry.
    database.x("update import_sessions set request_json=? where import_id=?",
               [json.dumps({"_feedback_profile": feedback.CANCELLATION_PROFILE}), view["import_id"]])
    before = {table: count(database, table, cid) for table in (
        "flows", "batches", "flow_sources", "import_sources", "import_members", "import_records")}
    parse = feedback.parse
    failures = []

    def observe(source, *, profile):
        try:
            return parse(source, profile=profile)
        except feedback.FeedbackImportError as error:
            failures.append((profile, error.code))
            raise

    monkeypatch.setattr(feedback, "parse", observe)
    failed = complete(client, cid, view)
    assert failed["stage"] == "error"
    assert database.q_raw("select error_code from import_sessions where import_id=?", [view["import_id"]]) == [
        {"error_code": "import_failed"}]
    assert complete(client, cid, view)["stage"] == "error"
    assert failures == [(feedback.CANCELLATION_PROFILE, "binary_member")] * 2
    assert before == {table: count(database, table, cid) for table in before}
    request = database.q_raw("select request_json from import_sessions where import_id=?", [view["import_id"]])[0]
    assert json.loads(request["request_json"])["_feedback_profile"] == feedback.CANCELLATION_PROFILE


@pytest.mark.parametrize("first,second", [(feedback.CANCELLATION_PROFILE, V4), (V4, feedback.CANCELLATION_PROFILE)])
def test_v3_v4_share_occurrences_without_reviving_cancelled_flows(client, database, make_case, monkeypatch, first, second):
    cid = make_case("Synthetic shared v3 v4 occurrences")
    content = paired_source()
    import_version(client, cid, content, monkeypatch, feedback.PREVIOUS_PROFILE)
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    for version in (first, second, first, feedback.IDENTITY_PROFILE, feedback.PREVIOUS_PROFILE):
        view, job = import_version(client, cid, content, monkeypatch, version)
        assert job["result"]["ok"] == 0
        assert count(database, "effective_flows", cid) == 1
        assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before
        assert client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json()["result"] == job["result"]
    keys = database.q_raw("select record_no,count(distinct record_key) n from import_records where case_id=? group by record_no", [cid])
    assert all(item["n"] == 1 for item in keys)


@pytest.mark.parametrize("later", [feedback.IDENTITY_PROFILE, feedback.CANCELLATION_PROFILE, V4])
def test_v4_cancellation_identity_guard_rejects_later_reinterpretation(client, database, make_case, monkeypatch, later):
    cid = make_case("Synthetic v4 identity guard")
    import_version(client, cid, paired_source(), monkeypatch, V4)
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    view, job = import_version(client, cid, paired_source(owner_name="Synthetic changed owner"), monkeypatch, later)
    assert job["result"]["ok"] == 0
    assert count(database, "effective_flows", cid) == 1
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before
    reasons = database.q_raw("select reason_code from import_records where source_id=? order by record_no", [view["import_id"]])
    assert reasons[0] == {"reason_code": "own_identity_conflict"}
    assert reasons[1] == {"reason_code": "own_identity_conflict"}
    packaged = case_packages.export_package(cid)[0]
    restored = case_packages.import_package(packaged)["case_id"]
    assert count(database, "effective_flows", restored) == 1


@pytest.mark.parametrize("with_table", [False, True])
def test_v4_cancellation_package_preserves_existing_format_rules(client, database, make_case, monkeypatch, with_table):
    cid = make_case("Synthetic v4 portable transport")
    members = _members(paired_source())
    path = "synthetic/transactions.csv"
    text = members[path].decode("gb18030")
    reader = list(csv.reader(io.StringIO(text, newline="")))
    column = [cell.strip() for cell in reader[0]].index("交易发生地")
    reader[1][column] = LOCATION
    stream = io.StringIO(newline="")
    csv.writer(stream, quoting=csv.QUOTE_ALL).writerows(reader)
    members[path] = stream.getvalue().encode("gb18030")
    view, job = import_version(client, cid, _archive(members), monkeypatch, V4)
    assert job["result"]["feedback"]["cancelled"] == 2
    if with_table:
        response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": (
            "synthetic.csv", "交易时间,交易金额,收付标志,交易账号,交易对手账卡号\n2026-01-02 10:00:00,2.00,出,SYN-C,SYN-D\n".encode())})
        assert response.status_code == 200, response.text
        assert complete(client, cid, response.json())["stage"] == "done"
    package = case_packages.export_package(cid)[0]
    assert json.loads(_members(package)["meta.json"])["format"] == 6
    restored = case_packages.import_package(package)["case_id"]
    assert count(database, "effective_flows", restored) == (2 if with_table else 1)
    assert count(database, "import_records", restored) == count(database, "import_records", cid)
    versions = database.q_raw("select parser_version from import_sources where case_id=? and row_kind='archive_record'", [restored])
    assert versions == [{"parser_version": V4}]
