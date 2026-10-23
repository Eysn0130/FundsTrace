"""Synthetic archive journals remain closed and portable without source ZIPs."""
from contextlib import contextmanager
import csv
import hashlib
import io
import json
import uuid
import zipfile

import duckdb
from fastapi import HTTPException
import pytest

from app import case_packages, feedback_import, importer


def _identity_digest(name, bank):
    return hashlib.sha256(json.dumps([name, bank], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _record_key(path, digest, row, identity=""):
    return hashlib.sha256(json.dumps(
        ["funds-trace-bank-feedback-v1", path, digest, row, identity],
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _seed(database, make_case, flow_row, direction="出", profile="funds-trace-bank-feedback-v1"):
    legacy = profile == "funds-trace-bank-feedback-v1"
    case_id = make_case(f"Synthetic feedback package {uuid.uuid4().hex[:8]}")
    source_id = uuid.uuid4().hex
    flow = flow_row(amount="12.34", balance="56.78", payer_bank="Synthetic own bank", payee_bank="Synthetic other bank")
    if direction != "出":
        flow["self_name"], flow["self_account"] = flow["payee_name"], flow["payee_account"]
    columns = ",".join(flow)
    flow_id = database.q(f"insert into flows(case_id,{columns}) values (?,{','.join('?' for _ in flow)}) returning id",
                         [case_id, *flow.values()])[0]["id"]
    database.x("insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,mapping_json,sheet,parser_version,row_kind) values (?,?, 'synthetic-feedback.zip',?,100,'B',?,'',?,'archive_record')",
               [source_id, case_id, "a" * 64, json.dumps(feedback_import.MAPPING, ensure_ascii=False), profile])
    paths = ["synthetic/Transaction.csv", "synthetic/Account.csv"]
    for path, role, count in zip(paths, ["transaction", "account"], [4 if legacy else 5, 1]):
        digest = hashlib.sha256(path.encode()).hexdigest()
        database.x("insert into import_members(case_id,source_id,member_path,role,sha256,size_bytes,encoding,row_count) values (?,?,?,?,?,100,'utf-8-sig',?)",
                   [case_id, source_id, path, role, digest, count])
    entries = [(1, paths[0], 2, "imported", "transaction_valid"),
               (2, paths[0], 3, "duplicate", "transaction_valid"),
               (3, paths[0], 4, "review" if legacy else "unresolved", "negative_unknown"),
               (4, paths[0], 5, "non_transaction", "feedback_only")]
    if not legacy:
        entries.append((5, paths[0], 6, "non_monetary", "zero_amount"))
    entries.append((5 if legacy else 6, paths[1], 2, "auxiliary", "auxiliary_table"))
    for number, path, row, disposition, reason in entries:
        details = {"role": "account" if disposition == "auxiliary" else "transaction"}
        identity = ""
        if number in (1, 2):
            details["own_identity_record"] = "5" if legacy else "6"
            details["source_direction"] = direction
            identity = _identity_digest(flow["self_name"], flow["payer_bank"] if direction == "出" else flow["payee_bank"])
            details["own_identity_sha256"] = identity
        if not legacy and disposition != "auxiliary":
            details["financial_effect"] = {"imported": "source_reported", "duplicate": "source_reported",
                                           "unresolved": "undetermined", "non_transaction": "not_a_flow",
                                           "non_monetary": "no_movement"}[disposition]
            if disposition in ("imported", "duplicate"):
                details.update(canonical_amount="12.34", signed_amount="12.34", reported_status="01")
            elif disposition == "unresolved":
                details.update(canonical_amount="-1.00", signed_amount="-1.00", validation_issues="negative_unknown")
            elif disposition == "non_monetary":
                details.update(canonical_amount="0.00", signed_amount="0.00", validation_issues="zero_amount")
            elif disposition == "non_transaction":
                details.update(signed_amount="", source_direction="")
        digest = hashlib.sha256(path.encode()).hexdigest()
        database.x("insert into import_records(case_id,source_id,record_no,member_path,source_row,record_key,disposition,reason_code,details_json) values (?,?,?,?,?,?,?,?,?)",
                   [case_id, source_id, number, path, row, _record_key(path, digest, row, identity),
                    disposition, reason, json.dumps(details, ensure_ascii=False)])
        if number in (1, 2):
            database.x("insert into flow_sources(case_id,source_id,source_row,flow_id,disposition) values (?,?,?,?,?)",
                       [case_id, source_id, number, flow_id, disposition])
    return case_id, source_id, flow_id


def _members(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _archive(members):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return stream.getvalue()


def _mutate(members, table, sql, tmp_path):
    path = tmp_path / f"input-{uuid.uuid4().hex}.parquet"
    target = tmp_path / f"output-{uuid.uuid4().hex}.parquet"
    path.write_bytes(members[f"{table}.parquet"])
    with duckdb.connect(config={"threads": 1}) as connection:
        connection.execute("create table entries as select * from read_parquet(?)", [str(path)])
        connection.execute(sql)
        connection.table("entries").write_parquet(str(target))
    members[f"{table}.parquet"] = target.read_bytes()


def _journal(database, table, case_id):
    fields = ",".join(field.strip() for field in case_packages.JOURNAL_PKG_COLS[table].split(",")
                      if field.strip() != "source_id")
    order = "record_no" if table == "import_records" else "member_path"
    return database.q(f"select {fields} from {table} where case_id=? order by {order}", [case_id])


@pytest.mark.parametrize("profile", ["funds-trace-bank-feedback-v1", "funds-trace-bank-feedback-v2"])
@pytest.mark.parametrize("direction", ["出", "进", "入"])
def test_feedback_journal_round_trip_remaps_links_and_excludes_originals(database, make_case, flow_row, direction, profile):
    case_id, source_id, flow_id = _seed(database, make_case, flow_row, direction, profile)
    content, _ = case_packages.export_package(case_id)
    members = _members(content)
    assert json.loads(members["meta.json"])["source_files"] == "not_included"
    assert {"import_members.parquet", "import_records.parquet"} <= set(members)
    assert not any(name.endswith((".csv", ".zip")) or "upload" in name for name in members)
    imported = case_packages.import_package(content)["case_id"]
    source = database.q("select source_id,filename,origin,row_kind from import_sources where case_id=?", [imported])[0]
    assert source["source_id"] != source_id
    assert source == {"source_id": source["source_id"], "filename": "synthetic-feedback.zip",
                      "origin": "package", "row_kind": "archive_record"}
    for table in case_packages.JOURNAL_PKG_COLS:
        assert _journal(database, table, imported) == _journal(database, table, case_id)
        assert {row["source_id"] for row in database.q(f"select source_id from {table} where case_id=?", [imported])} == {source["source_id"]}
    links = database.q("select source_row,flow_id,disposition from flow_sources where case_id=? order by source_row", [imported])
    assert [(row["source_row"], row["disposition"]) for row in links] == [(1, "imported"), (2, "duplicate")]
    assert len({row["flow_id"] for row in links}) == 1
    assert links[0]["flow_id"] != flow_id
    assert database.q("select count(*) n from import_sessions where case_id=?", [imported]) == [{"n": 0}]


def test_empty_legacy_case_keeps_optional_journals_absent(database, make_case):
    content, _ = case_packages.export_package(make_case())
    assert not set(f"{table}.parquet" for table in case_packages.JOURNAL_PKG_COLS) & set(_members(content))
    imported = case_packages.import_package(content)["case_id"]
    for table in case_packages.JOURNAL_PKG_COLS:
        assert database.q(f"select count(*) n from {table} where case_id=?", [imported]) == [{"n": 0}]


@pytest.mark.parametrize("direction,field", [
    ("出", "payer_name"), ("进", "payee_name"), ("入", "payee_name"),
    ("出", "payer_account"), ("进", "payee_account"),
])
def test_actual_own_side_must_match_canonical_identity(database, make_case, flow_row, tmp_path, direction, field):
    case_id, _, _ = _seed(database, make_case, flow_row, direction)
    members = _members(case_packages.export_package(case_id)[0])
    _mutate(members, "flows", f"update entries set {field}='Changed actual own side'", tmp_path)
    before = database.q("select id from cases order by id")
    with pytest.raises(HTTPException) as raised:
        case_packages.import_package(_archive(members))
    assert raised.value.status_code == 400
    assert database.q("select id from cases order by id") == before


@pytest.mark.parametrize("mutation", [
    "one_journal", "no_provenance", "no_journals", "profile", "mapping", "kind",
    "member_hash", "member_path", "member_absolute", "member_backslash", "member_role",
    "member_source", "member_duplicate", "member_fraction", "member_infinity", "member_limit",
    "row_count", "row_count_null", "record_gap", "record_fraction", "record_duplicate",
    "source_row_duplicate", "source_row_zero", "record_hash", "record_source", "record_disposition",
    "record_reason", "record_details", "record_nested", "identity_ref", "record_role",
    "record_event", "account_relation", "review_reasons", "detail_hash",
    "identity_hash", "identity_missing", "identity_on_review", "flow_own_name", "flow_own_bank", "flow_direction",
    "missing_link", "wrong_link", "non_transaction_link", "extra_column",
])
def test_tampered_feedback_journal_fails_before_creating_case(database, make_case, flow_row, tmp_path, mutation):
    case_id, _, _ = _seed(database, make_case, flow_row)
    members = _members(case_packages.export_package(case_id)[0])
    if mutation == "one_journal":
        members.pop("import_records.parquet")
    elif mutation == "no_provenance":
        members.pop("import_sources.parquet")
        members.pop("flow_sources.parquet")
    elif mutation == "no_journals":
        members.pop("import_members.parquet")
        members.pop("import_records.parquet")
    else:
        table, sql = {
            "profile": ("import_sources", "update entries set parser_version='invented'"),
            "mapping": ("import_sources", "update entries set mapping_json='{}'"),
            "kind": ("import_sources", "update entries set row_kind='csv_record'"),
            "member_hash": ("import_members", "update entries set sha256=repeat('b',64)"),
            "member_path": ("import_members", "update entries set member_path='../Transaction.csv' where role='transaction'"),
            "member_absolute": ("import_members", "update entries set member_path='/Transaction.csv' where role='transaction'"),
            "member_backslash": ("import_members", "update entries set member_path='synthetic\\Transaction.csv' where role='transaction'"),
            "member_role": ("import_members", "update entries set role='invented'"),
            "member_source": ("import_members", "update entries set source_id='aabbccddeeff'"),
            "member_duplicate": ("import_members", "insert into entries select * from entries limit 1"),
            "member_fraction": ("import_members", "alter table entries alter column size_bytes type double; update entries set size_bytes=1.5"),
            "member_infinity": ("import_members", "alter table entries alter column row_count type double; update entries set row_count='Infinity'::double"),
            "member_limit": ("import_members", "update entries set size_bytes=134217729"),
            "row_count": ("import_members", "update entries set row_count=row_count+1"),
            "row_count_null": ("import_members", "update entries set row_count=null"),
            "record_gap": ("import_records", "update entries set record_no=100 where record_no=5"),
            "record_fraction": ("import_records", "alter table entries alter column record_no type double; update entries set record_no=1.5 where record_no=1"),
            "record_duplicate": ("import_records", "insert into entries select * from entries limit 1"),
            "source_row_duplicate": ("import_records", "update entries set source_row=2 where record_no=2"),
            "source_row_zero": ("import_records", "update entries set source_row=0 where record_no=1"),
            "record_hash": ("import_records", "update entries set record_key=repeat('b',64)"),
            "record_source": ("import_records", "update entries set source_id='aabbccddeeff'"),
            "record_disposition": ("import_records", "update entries set disposition='auxiliary' where record_no=1"),
            "record_reason": ("import_records", "update entries set reason_code='invented'"),
            "record_details": ("import_records", "update entries set details_json='{\"raw_row\":\"not allowed\"}'"),
            "record_nested": ("import_records", "update entries set details_json='{\"reported_status\":{\"raw\":\"not allowed\"}}'"),
            "identity_ref": ("import_records", "update entries set details_json='{\"own_identity_record\":\"3\"}' where record_no=1"),
            "record_role": ("import_records", "update entries set details_json='{\"role\":\"phone\"}' where record_no=1"),
            "record_event": ("import_records", "update entries set details_json='{\"event_type\":\"invented\"}' where record_no=1"),
            "account_relation": ("import_records", "update entries set details_json='{\"account_card_relation\":\"invented\"}' where record_no=1"),
            "review_reasons": ("import_records", "update entries set details_json='{\"review_reasons\":\"invented\"}' where record_no=3"),
            "detail_hash": ("import_records", "update entries set details_json='{\"signed_amount_sha256\":\"bad\"}' where record_no=3"),
            "identity_hash": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"own_identity_sha256\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\"}') where record_no=1"),
            "identity_missing": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"own_identity_sha256\":null}') where record_no=1"),
            "identity_on_review": ("import_records", "update entries set details_json='{\"own_identity_sha256\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\"}' where record_no=3"),
            "flow_own_name": ("flows", "update entries set self_name='Changed canonical own name'"),
            "flow_own_bank": ("flows", "update entries set payer_bank='Changed canonical own bank'"),
            "flow_direction": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"source_direction\":\"进\"}') where record_no=1"),
            "missing_link": ("flow_sources", "delete from entries where source_row=1"),
            "wrong_link": ("flow_sources", "update entries set disposition='duplicate' where source_row=1"),
            "non_transaction_link": ("flow_sources", "insert into entries select source_id,4,flow_id,'imported' from entries limit 1"),
            "extra_column": ("import_records", "alter table entries add column raw_row varchar"),
        }[mutation]
        _mutate(members, table, sql, tmp_path)
    before = database.q("select id from cases order by id")
    with pytest.raises(HTTPException) as raised:
        case_packages.import_package(_archive(members))
    assert raised.value.status_code == 400
    assert database.q("select id from cases order by id") == before
    assert database.q("select count(*) n from import_records") == [{"n": 5}]


@pytest.mark.parametrize("mutation", [
    "missing_amount", "fake_zero", "nan_zero", "fractional_zero", "raw_zero_mismatch", "zero_flow",
    "unknown_status", "wrong_effect", "v2_review", "unresolved_effect", "unresolved_zero", "unresolved_hidden_zero",
    "unresolved_reason", "unknown_issues", "non_monetary_link", "unresolved_link", "accepted_issues",
    "non_transaction_amount", "legacy_review_metadata", "flow_amount", "unknown_profile",
    "feedback_amount", "feedback_direction", "feedback_amount_hash", "feedback_amount_length",
    "feedback_direction_hash", "feedback_direction_length", "feedback_issues", "aux_semantics", "aux_role_missing",
])
def test_invalid_v2_automatic_dispositions_fail_closed(database, make_case, flow_row, tmp_path, mutation):
    case_id, _, _ = _seed(database, make_case, flow_row, profile="funds-trace-bank-feedback-v2")
    members = _members(case_packages.export_package(case_id)[0])
    table, sql = {
        "missing_amount": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"canonical_amount\":null}') where record_no=5"),
        "fake_zero": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"canonical_amount\":\"1.00\"}') where record_no=5"),
        "nan_zero": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"canonical_amount\":\"NaN\"}') where record_no=5"),
        "fractional_zero": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"canonical_amount\":\"0.001\"}') where record_no=5"),
        "raw_zero_mismatch": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"signed_amount\":\"1.00\"}') where record_no=5"),
        "zero_flow": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"canonical_amount\":\"0.00\",\"signed_amount\":\"0.00\"}') where record_no=1"),
        "unknown_status": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"reported_status\":\"00\"}') where record_no=1"),
        "wrong_effect": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"financial_effect\":\"undetermined\"}') where record_no=1"),
        "v2_review": ("import_records", "update entries set disposition='review' where record_no=3"),
        "unresolved_effect": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"financial_effect\":\"source_reported\"}') where record_no=3"),
        "unresolved_zero": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"canonical_amount\":\"0.00\",\"signed_amount\":\"0.00\"}') where record_no=3"),
        "unresolved_hidden_zero": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"canonical_amount\":null,\"signed_amount\":\"0.00\"}') where record_no=3"),
        "unresolved_reason": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"validation_issues\":\"invalid_time\"}') where record_no=3"),
        "unknown_issues": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"validation_issues\":\"invented\"}') where record_no=5"),
        "non_monetary_link": ("flow_sources", "insert into entries select source_id,5,flow_id,'imported' from entries limit 1"),
        "unresolved_link": ("flow_sources", "insert into entries select source_id,3,flow_id,'imported' from entries limit 1"),
        "accepted_issues": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"validation_issues\":\"invalid_time\"}') where record_no=1"),
        "non_transaction_amount": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"canonical_amount\":\"0.00\"}') where record_no=4"),
        "legacy_review_metadata": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"review_reasons\":\"negative_unknown\"}') where record_no=3"),
        "flow_amount": ("flows", "update entries set amount=99.99"),
        "unknown_profile": ("import_sources", "update entries set parser_version='funds-trace-bank-feedback-v99'"),
        "feedback_amount": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"signed_amount\":\"not money\"}') where record_no=4"),
        "feedback_direction": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"source_direction\":\"出\"}') where record_no=4"),
        "feedback_amount_hash": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"signed_amount_sha256\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}') where record_no=4"),
        "feedback_amount_length": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"signed_amount_length\":\"81\"}') where record_no=4"),
        "feedback_direction_hash": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"source_direction_sha256\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"}') where record_no=4"),
        "feedback_direction_length": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"source_direction_length\":\"81\"}') where record_no=4"),
        "feedback_issues": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"validation_issues\":\"invalid_time\"}') where record_no=4"),
        "aux_semantics": ("import_records", "update entries set details_json=json_merge_patch(details_json,'{\"financial_effect\":\"not_a_flow\"}') where record_no=6"),
        "aux_role_missing": ("import_records", "update entries set details_json='{}' where record_no=6"),
    }[mutation]
    _mutate(members, table, sql, tmp_path)
    before = database.q("select id from cases order by id")
    with pytest.raises(HTTPException) as raised:
        case_packages.import_package(_archive(members))
    assert raised.value.status_code == 400
    assert database.q("select id from cases order by id") == before


@pytest.mark.parametrize("field,value", [("canonical_amount", "12.34"), ("financial_effect", "source_reported"),
                                         ("validation_issues", "invalid_time")])
def test_v1_source_does_not_accept_v2_metadata(database, make_case, flow_row, tmp_path, field, value):
    case_id, _, _ = _seed(database, make_case, flow_row)
    members = _members(case_packages.export_package(case_id)[0])
    patch = json.dumps({field: value})
    _mutate(members, "import_records", f"update entries set details_json=json_merge_patch(details_json,'{patch}') where record_no=1", tmp_path)
    with pytest.raises(HTTPException) as raised:
        case_packages.import_package(_archive(members))
    assert raised.value.status_code == 400


def test_v1_v2_keep_the_independent_physical_record_key():
    path = "synthetic/transactions.csv"
    text = io.StringIO(newline="")
    row = {column: "" for column in feedback_import.COLUMNS}
    row.update({"交易账号": "SYN-A", "交易时间": "2026-01-01 10:00:00", "交易金额": "12.34",
                "收付标志": "出", "交易对手账卡号": "SYN-B", "交易币种": "CNY", "交易是否成功": "01"})
    writer = csv.DictWriter(text, fieldnames=feedback_import.COLUMNS)
    writer.writeheader()
    writer.writerow(row)
    payload = text.getvalue().encode("utf-8")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(path, payload)
    old = feedback_import.parse(io.BytesIO(stream.getvalue()), profile="funds-trace-bank-feedback-v1")
    new = feedback_import.parse(io.BytesIO(stream.getvalue()), profile="funds-trace-bank-feedback-v2")
    expected = _record_key(path, hashlib.sha256(payload).hexdigest(), 2, _identity_digest("", ""))
    assert old["rows"][0]["_record_key"] == new["rows"][0]["_record_key"] == expected
    assert old["profile"] != new["profile"]
    assert "canonical_amount" not in old["records"][0]["details"]
    assert new["records"][0]["details"]["canonical_amount"] == "12.34"


def test_v2_provenance_reimport_reuses_existing_v1_flow(database, make_case, flow_row):
    case_id, old_source, flow_id = _seed(database, make_case, flow_row)
    row = database.q_raw("select * exclude(id,case_id,batch_id) from flows where id=?", [flow_id])[0]
    key = database.q_raw("select record_key from import_records where case_id=? and record_no=1", [case_id])[0]["record_key"]
    row.update(_source_row=1, _source_sheet="", _source_row_kind="archive_record", _record_key=key)
    new_source = uuid.uuid4().hex
    source = {"source_id": new_source, "filename": "synthetic-feedback.zip", "sha256": "a" * 64,
              "size_bytes": 100, "mode": "B", "mapping": feedback_import.MAPPING, "sheet": "",
              "parser_version": "funds-trace-bank-feedback-v2", "row_kind": "archive_record"}
    result = importer.commit_rows(source["filename"], "B", [row], {"total": 1, "ok": 1, "skipped": 0},
                                  case_id=case_id, provenance=source)
    assert (result["ok"], result["rows_dup"]) == (0, 1)
    assert database.q("select id from flows where case_id=?", [case_id]) == [{"id": flow_id}]
    assert database.q("select source_id,flow_id,disposition from flow_sources where source_id=?", [new_source]) == [
        {"source_id": new_source, "flow_id": flow_id, "disposition": "duplicate"}]
    assert old_source != new_source


def test_v2_identity_conflict_round_trip_keeps_the_unresolved_record_without_a_flow(database, make_case, flow_row):
    case_id, source_id, _ = _seed(database, make_case, flow_row, profile="funds-trace-bank-feedback-v2")
    details = {"role": "transaction", "canonical_amount": "12.34", "signed_amount": "12.34",
               "source_direction": "出", "reported_status": "01", "financial_effect": "undetermined",
               "validation_issues": "own_identity_conflict"}
    database.x("update import_records set reason_code='own_identity_conflict',details_json=? where source_id=? and record_no=3",
               [json.dumps(details, ensure_ascii=False), source_id])
    content, _ = case_packages.export_package(case_id)
    restored = case_packages.import_package(content)["case_id"]
    record = database.q("select disposition,reason_code,details_json from import_records where case_id=? and record_no=3", [restored])[0]
    assert record["disposition"] == "unresolved" and record["reason_code"] == "own_identity_conflict"
    assert json.loads(record["details_json"]) == details
    assert database.q("select count(*) n from flow_sources where case_id=? and source_row=3", [restored]) == [{"n": 0}]
    assert database.q("select count(*) n,sum(amount) amount from flows where case_id=?", [restored]) == [{"n": 1, "amount": "12.34"}]


@pytest.mark.parametrize("primary_reason", [False, True])
def test_v1_journal_rejects_the_new_identity_conflict_reason(database, make_case, flow_row, primary_reason):
    case_id, source_id, _ = _seed(database, make_case, flow_row)
    details = json.dumps({"review_reasons": "own_identity_conflict"})
    reason = "own_identity_conflict" if primary_reason else "negative_unknown"
    database.x("update import_records set reason_code=?,details_json=? where source_id=? and record_no=3",
               [reason, details, source_id])
    content, _ = case_packages.export_package(case_id)
    before = database.q("select id from cases order by id")
    with pytest.raises(HTTPException) as raised:
        case_packages.import_package(content)
    assert raised.value.status_code == 400
    assert database.q("select id from cases order by id") == before


@pytest.mark.parametrize("profile", ["funds-trace-bank-feedback-v1", "funds-trace-bank-feedback-v2"])
def test_removed_transaction_journals_remain_portable(database, make_case, flow_row, profile):
    case_id, _, flow_id = _seed(database, make_case, flow_row, profile=profile)
    database.x("update import_records set disposition='removed',reason_code='flow_deleted' where case_id=? and record_no in (1,2)", [case_id])
    database.x("delete from flow_sources where case_id=?", [case_id])
    database.x("delete from flows where case_id=? and id=?", [case_id, flow_id])
    content, _ = case_packages.export_package(case_id)
    imported = case_packages.import_package(content)["case_id"]
    assert database.q("select disposition,reason_code from import_records where case_id=? order by record_no limit 2", [imported]) == [
        {"disposition": "removed", "reason_code": "flow_deleted"},
        {"disposition": "removed", "reason_code": "flow_deleted"},
    ]
    assert database.q("select count(*) n from flows where case_id=?", [imported]) == [{"n": 0}]


def test_journal_package_import_and_case_delete_rollback_atomically(database, make_case, flow_row, monkeypatch):
    case_id, _, _ = _seed(database, make_case, flow_row)
    other, _, _ = _seed(database, make_case, flow_row)
    content, _ = case_packages.export_package(case_id)
    original = database.transaction

    @contextmanager
    def fail_commit():
        with original() as connection:
            yield connection
            raise RuntimeError("synthetic journal commit failure")

    monkeypatch.setattr(database, "transaction", fail_commit)
    before = database.q("select id from cases order by id")
    with pytest.raises(RuntimeError, match="synthetic journal commit failure"):
        case_packages.import_package(content)
    assert database.q("select id from cases order by id") == before
    with pytest.raises(RuntimeError, match="synthetic journal commit failure"):
        case_packages.delete_package_case(case_id)
    assert database.q("select count(*) n from import_records where case_id=?", [case_id]) == [{"n": 5}]
    monkeypatch.setattr(database, "transaction", original)
    case_packages.delete_package_case(case_id)
    for table in ("import_records", "import_members", "flow_sources", "import_sources", "flows"):
        assert database.q(f"select count(*) n from {table} where case_id=?", [case_id]) == [{"n": 0}]
    assert database.q("select count(*) n from import_records where case_id=?", [other]) == [{"n": 5}]
