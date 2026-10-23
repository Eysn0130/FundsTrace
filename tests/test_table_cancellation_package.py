"""Synthetic format-4 closure for the independent table cancellation profile."""
import io
import json
import uuid

from fastapi import HTTPException
import pytest

from app import case_packages, table_archive, table_import
from app.services import imports
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_feedback_package import _archive, _journal, _members, _mutate, _seed as _seed_feedback
from test_table_cancellations import round_trip, source, transaction
from test_table_package import _seed_table


V1 = "funds-trace-table-archive-v1"
V2 = "funds-trace-table-archive-v2"
V3 = "funds-trace-table-archive-v3"


def _plan(rows=None, profile=V3):
    return table_archive.parse(io.BytesIO(source(rows or round_trip())[1]), profile=profile)


def _seed_plan(database, case_id, plan, *, shared=None):
    source_id = uuid.uuid4().hex
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,
        mode,mapping_json,sheet,parser_version,row_kind) values (?,?,?, ?,100,?,?, '',?,'table_record')""",
               [source_id, case_id, "synthetic-table.zip", "a" * 64,
                plan["mode"], json.dumps(plan["mapping"]), plan["profile"]])
    for member in plan["members"]:
        fields = tuple(member)
        database.x(f"insert into import_members(case_id,source_id,{','.join(fields)}) "
                   f"values (?, ?,{','.join('?' for _ in fields)})",
                   [case_id, source_id, *member.values()])
    flow_ids = {}
    for row in plan["rows"]:
        digest = table_import.flow_digest(row)
        if shared is not None and digest in shared:
            flow_id = shared[digest]
        else:
            fields = (*table_import.FLOW_FIELDS, "self_account", "self_name")
            flow_id = database.q(f"insert into flows(case_id,{','.join(fields)}) "
                                 f"values (?,{','.join('?' for _ in fields)}) returning id",
                                 [case_id, *(row[field] for field in fields)])[0]["id"]
            if shared is not None:
                shared[digest] = flow_id
        flow_ids[row["_source_row"]] = flow_id
    for record in plan["records"]:
        disposition = "imported" if record["disposition"] == "accepted" else record["disposition"]
        database.x("""insert into import_records(case_id,source_id,record_no,member_path,
            source_row,record_key,disposition,reason_code,details_json) values (?,?,?,?,?,?,?,?,?)""",
                   [case_id, source_id, record["record_no"], record["member_path"],
                    record["source_row"], record["record_key"], disposition, record["reason_code"],
                    json.dumps(record["details"], ensure_ascii=False)])
        if record["record_no"] in flow_ids:
            database.x("insert into flow_sources(case_id,source_id,source_row,flow_id,disposition) values (?,?,?,?,?)",
                       [case_id, source_id, record["record_no"], flow_ids[record["record_no"]], disposition])
    return source_id, flow_ids


def _assert_rejected(database, members):
    before = database.q("select id from cases order by id")
    with pytest.raises(HTTPException) as raised:
        case_packages.import_package(_archive(members))
    assert raised.value.status_code == 400
    assert database.q("select id from cases order by id") == before


def test_v3_profile_can_round_trip_without_a_cancellation(database, make_case, flow_row):
    cid, _, _ = _seed_table(database, make_case, flow_row, profile=V3, filename="synthetic.zip")
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert _journal(database, "import_records", restored) == _journal(database, "import_records", cid)


def test_fresh_v3_cancellation_has_closed_format4_round_trip(database, make_case):
    cid = make_case("Synthetic table cancellation package")
    _seed_plan(database, cid, _plan())
    package = case_packages.export_package(cid)[0]
    members = _members(package)
    assert json.loads(members["meta.json"])["format"] == 4
    assert json.loads(members["meta.json"])["source_files"] == "not_included"
    assert not any(path.endswith((".csv", ".zip")) for path in members)
    restored = case_packages.import_package(package)["case_id"]
    for table in case_packages.JOURNAL_PKG_COLS:
        assert _journal(database, table, restored) == _journal(database, table, cid)
    assert database.q("select source_row from flow_sources where case_id=?", [restored]) == [{"source_row": 1}]
    assert database.q("select disposition from import_records where case_id=? order by record_no", [restored]) == [
        {"disposition": "imported"}, {"disposition": "cancelled"}, {"disposition": "cancelled"}]


def test_v1_v2_v3_and_feedback_round_trip_preserves_each_semantic_version(database, make_case, flow_row):
    cid, _, _ = _seed_feedback(database, make_case, flow_row, profile="funds-trace-bank-feedback-v3")
    shared = {}
    for profile in (V1, V2, V3):
        _seed_plan(database, cid, _plan(profile=profile), shared=shared)
    package = case_packages.export_package(cid)[0]
    restored = case_packages.import_package(package)["case_id"]
    assert sorted(row["parser_version"] for row in database.q(
        "select parser_version from import_sources where case_id=?", [restored])) == sorted(
            [V1, V2, V3, "funds-trace-bank-feedback-v3"])
    assert database.q_raw("select details_json from import_records where case_id=? order by details_json", [restored]) == database.q_raw(
        "select details_json from import_records where case_id=? order by details_json", [cid])
    assert database.q("select origin from import_sources where case_id=? and row_kind='archive_record'", [restored]) == [{"origin": "package"}]


@pytest.mark.parametrize("profile", [V1, V2, table_import.ROLE_PROFILE, table_import.PROFILE])
def test_new_cancellation_notes_cannot_be_disguised_as_old_profiles(database, make_case, tmp_path, profile):
    cid = make_case("Synthetic table version spoof")
    _seed_plan(database, cid, _plan())
    members = _members(case_packages.export_package(cid)[0])
    filename = "synthetic.csv" if profile == table_import.PROFILE else "synthetic.zip"
    _mutate(members, "import_sources", f"update entries set parser_version='{profile}',filename='{filename}'", tmp_path)
    _assert_rejected(database, members)


def test_v2_reference_notes_remain_valid_after_v3_is_current(database, make_case):
    cid = make_case("Synthetic frozen v2 reference notes")
    _seed_plan(database, cid, _plan(profile=V2))
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert _journal(database, "import_records", restored) == _journal(database, "import_records", cid)


@pytest.mark.parametrize("patch,record_no", [
    ({"paired_record_no": "1"}, 2),
    ({"cancellation_witness_record_no": "3"}, 2),
    ({"cancellation_direction": "in"}, 3),
    ({"cancellation_amount": "-9.99"}, 3),
    ({"cancellation_balance": "99.99"}, 3),
    ({"cancellation_time": "2026-01-01 10:01:00.000000"}, 3),
    ({"cancellation_reference_sha256": "b" * 64}, 3),
    ({"cancellation_scope_sha256": "b" * 64}, 3),
    ({"cancellation_status": "other"}, 3),
    ({"cancellation_status": "invented"}, 3),
    ({"cancellation_action": "conflict"}, 3),
    ({"cancellation_quality": "other"}, 1),
    ({"cancellation_own_name_sha256": "b" * 64}, 3),
    ({"cancellation_merchant_sha256": "b" * 64}, 3),
    ({"flow_sha256": "b" * 64}, 1),
    ({"cancellation_policy": None}, 1),
    ({"cancellation_movement": None}, 1),
])
def test_tampered_v3_pair_fails_before_creating_a_case(database, make_case, tmp_path, patch, record_no):
    cid = make_case("Synthetic cancellation evidence tampering")
    _seed_plan(database, cid, _plan())
    members = _members(case_packages.export_package(cid)[0])
    encoded = json.dumps(patch, separators=(",", ":"))
    _mutate(members, "import_records", "update entries set details_json=json_merge_patch(details_json,'"
            + encoded + f"') where record_no={record_no}", tmp_path)
    _assert_rejected(database, members)


@pytest.mark.parametrize("field,value", [
    ("self_account", "SYN-DIFFERENT"), ("self_name", "Synthetic different owner"),
    ("amount", "2.00"), ("balance", "99.00"), ("currency", "USD"),
    ("trade_time", "2026-01-01 09:59:00"),
])
def test_witness_actual_facts_are_bound_even_when_digest_is_forged_to_match(database, make_case, tmp_path, field, value):
    cid = make_case("Synthetic actual witness binding")
    source_id, links = _seed_plan(database, cid, _plan())
    database.x(f"update flows set {field}=? where id=?", [value, links[1]])
    changed = database.q("select * from flows where id=?", [links[1]])[0]
    database.x("update import_records set details_json=json_merge_patch(details_json,?) where source_id=? and record_no=1",
               [json.dumps({"flow_sha256": table_import.flow_digest(changed)}), source_id])
    _assert_rejected(database, _members(case_packages.export_package(cid)[0]))


@pytest.mark.parametrize("field,value", [("trade_time", "2026-01-01 10:01:00"),
                                         ("trade_no", "SYN-PAIR"), ("self_account", "SYN-OTHER-SCOPE")])
def test_nonselected_accepted_observations_are_bound_to_actual_facts(database, make_case, field, value):
    rows = round_trip() + [transaction(**{"交易时间": "2026-01-01 10:03:00", "交易流水号": "SYN-OTHER", "交易余额": "99.00"})]
    cid = make_case("Synthetic complete actual index binding")
    source_id, links = _seed_plan(database, cid, _plan(rows))
    database.x(f"update flows set {field}=? where id=?", [value, links[4]])
    changed = database.q("select * from flows where id=?", [links[4]])[0]
    database.x("update import_records set details_json=json_merge_patch(details_json,?) where source_id=? and record_no=4",
               [json.dumps({"flow_sha256": table_import.flow_digest(changed)}), source_id])
    _assert_rejected(database, _members(case_packages.export_package(cid)[0]))


def test_valid_witness_with_voucher_fallback_keeps_empty_primary_reference(database, make_case):
    rows = round_trip()
    rows[0].update({"交易流水号": "", "凭证号": "SYN-WITNESS-VOUCHER"})
    cid = make_case("Synthetic fallback witness")
    plan = _plan(rows)
    assert len(plan["cancellations"]) == 1
    _seed_plan(database, cid, plan)
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert _journal(database, "import_records", restored) == _journal(database, "import_records", cid)


@pytest.mark.parametrize("field,value", [("self_account", "SYN-HISTORY"), ("self_name", "Synthetic historical owner"),
                                         ("amount", "11.00"), ("summary", "Synthetic historical changed facts")])
def test_cancelled_original_must_match_retained_historical_flow(database, make_case, tmp_path, field, value):
    cid = make_case("Synthetic original history binding")
    old_source, old_links = _seed_plan(database, cid, _plan(profile=V2))
    _seed_plan(database, cid, _plan())
    database.x(f"update flows set {field}=? where id=?", [value, old_links[2]])
    changed = database.q("select * from flows where id=?", [old_links[2]])[0]
    database.x("update import_records set details_json=json_merge_patch(details_json,?) where source_id=? and record_no=2",
               [json.dumps({"flow_sha256": table_import.flow_digest(changed)}), old_source])
    _assert_rejected(database, _members(case_packages.export_package(cid)[0]))


def test_removed_witness_retains_proof_and_pair_on_round_trip(database, make_case):
    cid = make_case("Synthetic removed witness proof")
    source_id, links = _seed_plan(database, cid, _plan())
    database.x("delete from flow_sources where source_id=?", [source_id])
    database.x("delete from flows where id=?", [links[1]])
    database.x("update import_records set disposition='removed',reason_code='flow_deleted' where source_id=? and record_no=1", [source_id])
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert _journal(database, "import_records", restored) == _journal(database, "import_records", cid)
    assert database.q("select count(*) n from flows where case_id=?", [restored]) == [{"n": 0}]


@pytest.mark.parametrize("root_kind", ["positive", "zero"])
def test_forward_cancellation_chain_keeps_its_trusted_root(database, make_case, root_kind):
    rows = round_trip()
    if root_kind == "zero":
        rows[0]["交易金额"] = "0.00"
    rows += [transaction(**{"交易时间": "2026-01-01 10:03:00", "交易金额": "20.00", "交易余额": "80.00", "交易流水号": "SYN-NEXT"}),
             transaction(**{"交易时间": "2026-01-01 10:04:00", "交易金额": "-20.00", "摘要说明": "撤销", "交易流水号": "SYN-NEXT"})]
    cid = make_case("Synthetic rooted cancellation chain")
    plan = _plan(rows)
    assert len(plan["cancellations"]) == 2
    _seed_plan(database, cid, plan)
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert _journal(database, "import_records", restored) == _journal(database, "import_records", cid)


@pytest.mark.parametrize("mutation", ["missing_observations", "third_reference", "tied_time", "missing_time", "unknown_movement"])
def test_full_member_index_prevents_hidden_ambiguity(database, make_case, tmp_path, mutation):
    rows = round_trip() + [transaction(**{"交易时间": "2026-01-01 10:03:00", "交易流水号": "SYN-OTHER", "交易余额": "99.00"})]
    cid = make_case("Synthetic member observation completeness")
    _seed_plan(database, cid, _plan(rows))
    members = _members(case_packages.export_package(cid)[0])
    if mutation == "missing_observations":
        patch = dict.fromkeys(("cancellation_policy", "cancellation_scope_sha256", "cancellation_time",
                               "cancellation_reference_sha256", "cancellation_movement"))
    elif mutation == "third_reference":
        patch = {"cancellation_reference_sha256": _plan()["records"][1]["details"]["cancellation_reference_sha256"]}
    elif mutation == "tied_time":
        patch = {"cancellation_time": "2026-01-01 10:01:00.000000"}
    elif mutation == "missing_time":
        patch = {"cancellation_time": ""}
    else:
        patch = {"cancellation_movement": "unknown"}
    _mutate(members, "import_records", "update entries set details_json=json_merge_patch(details_json,'"
            + json.dumps(patch) + "') where record_no=4", tmp_path)
    _assert_rejected(database, members)


def test_negative_leg_with_retained_manual_flow_cannot_claim_cancellation(database, make_case):
    cid = make_case("Synthetic manual negative history conflict")
    old_plan = _plan(profile=V2)
    old_source, links = _seed_plan(database, cid, old_plan)
    fields = (*table_import.FLOW_FIELDS, "self_account", "self_name")
    old = database.q("select * from flows where id=?", [links[2]])[0]
    old.update(trade_time="2026-01-01 10:02:00", summary="冲正")
    manual = database.q(f"insert into flows(case_id,{','.join(fields)}) values (?,{','.join('?' for _ in fields)}) returning id",
                        [cid, *(old[field] for field in fields)])[0]["id"]
    details = old_plan["records"][2]["details"].copy()
    details.pop("validation_issues", None)
    details.update(financial_effect="source_reported", flow_sha256=table_import.flow_digest(old))
    database.x("update import_records set disposition='imported',reason_code='transaction_valid',details_json=? where source_id=? and record_no=3",
               [json.dumps(details), old_source])
    database.x("insert into flow_sources(case_id,source_id,source_row,flow_id,disposition) values (?,?,3,?,'imported')", [cid, old_source, manual])
    _seed_plan(database, cid, _plan())
    _assert_rejected(database, _members(case_packages.export_package(cid)[0]))


def test_shared_historical_flow_keeps_the_other_valid_physical_source(database, make_case):
    cid = make_case("Synthetic shared retained history")
    shared = {}
    _seed_plan(database, cid, _plan(profile=V2), shared=shared)
    _seed_plan(database, cid, _plan([round_trip()[1]], profile=V2), shared=shared)
    _seed_plan(database, cid, _plan(), shared=shared)
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    shared_flows = database.q("""select f.id,count(*) n from flows f join flow_sources s on s.flow_id=f.id
        where f.case_id=? and f.amount=10.00 group by f.id""", [restored])
    assert len(shared_flows) == 1 and shared_flows[0]["n"] == 2
    assert database.q("select count(*) n from flow_sources f join import_records r on r.source_id=f.source_id and r.record_no=f.source_row where r.case_id=? and r.disposition='cancelled'", [restored]) == [{"n": 0}]


def test_revoked_pair_preserves_a_different_manual_history_without_reinterpreting_it(database, make_case):
    from app import table_cancellations

    cid = make_case("Synthetic revoked pair history portability")
    old_source, old_links = _seed_plan(database, cid, _plan(profile=V2))
    database.x("update flows set amount=11.00,self_account='SYN-HISTORICAL' where id=?", [old_links[2]])
    old_flow = database.q("select * from flows where id=?", [old_links[2]])[0]
    database.x("update import_records set details_json=json_merge_patch(details_json,?) where source_id=? and record_no=2",
               [json.dumps({"flow_sha256": table_import.flow_digest(old_flow)}), old_source])
    plan = table_cancellations.revoke_pairs(_plan(), [2])
    assert not plan["cancellations"]
    _seed_plan(database, cid, plan)
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert database.q_raw("select amount,self_account from flows where case_id=? order by amount", [restored]) == database.q_raw(
        "select amount,self_account from flows where case_id=? order by amount", [cid])
    assert database.q_raw("select details_json from import_records where case_id=? order by details_json", [restored]) == database.q_raw(
        "select details_json from import_records where case_id=? order by details_json", [cid])


@pytest.mark.parametrize("history_difference", ["witness_summary", "observer_trade_no"])
def test_public_lifecycle_round_trip_keeps_other_source_interpretations(client, database, make_case, history_difference):
    rows = round_trip()
    for row in rows:
        row["凭证号"] = row["交易流水号"]
    if history_difference == "witness_summary":
        for row in rows:
            row["摘要说明"] = "Synthetic pair summary" if row["交易金额"] != "-10.00" else "冲正"
            row["商户名称"] = row["摘要说明"]
        rows[0]["商户名称"] = "Synthetic historical witness summary"
        field, column, changed_number = "summary", "商户名称", 1
    else:
        rows.append(transaction(**{"交易时间": "2026-01-01 10:03:00", "交易金额": "2.00",
                                   "交易余额": "98.00", "交易流水号": "SYN-OBSERVER", "凭证号": "SYN-OBSERVER"}))
        for row in rows:
            row["商户名称"] = row["交易流水号"]
        rows[3]["商户名称"] = "SYN-HISTORICAL-OBSERVER"
        field, column, changed_number = "trade_no", "商户名称", 4
    payload, archive = source(rows, path="table.csv")
    cid = make_case("Synthetic independent source interpretation package")
    old = preview(client, cid, payload)
    response = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview", json={
        "mode": old["mode"], "mapping": {**old["mapping"], field: column}})
    assert response.status_code == 200, response.text
    old = response.json()
    assert complete(client, cid, old)["stage"] == "done"
    current = preview(client, cid, archive, "synthetic.zip")
    assert current["table_summary"]["cancelled"] == 2
    result = complete(client, cid, current)
    assert result["stage"] == "done", result
    current_records = client.get(f"/api/v1/cases/{cid}/sources/{current['import_id']}/report").json()["items"]
    assert [record["disposition"] for record in current_records[1:3]] == ["cancelled", "cancelled"]
    old_flow = database.q_raw("""select f.* from flows f join flow_sources s on s.flow_id=f.id
        where s.source_id=? and s.source_row=?""", [old["import_id"], changed_number])[0]
    current_flow = database.q_raw("""select f.* from flows f join flow_sources s on s.flow_id=f.id
        where s.source_id=? and s.source_row=?""", [current["import_id"], changed_number])[0]
    assert old_flow[field] != current_flow[field]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    facts = ",".join((*table_import.FLOW_FIELDS, "self_account", "self_name"))
    assert database.q_raw(f"select {facts} from flows where case_id=? order by trade_no,summary", [restored]) == database.q_raw(
        f"select {facts} from flows where case_id=? order by trade_no,summary", [cid])
    journal = """select s.parser_version,r.record_no,r.disposition,r.reason_code,r.details_json
        from import_records r join import_sources s on s.source_id=r.source_id
        where r.case_id=? order by s.parser_version,r.record_no"""
    assert database.q_raw(journal, [restored]) == database.q_raw(journal, [cid])


@pytest.mark.parametrize("kind,expected_cancelled,expected_unresolved", [
    ("unpaired_negative", 0, 1),
    ("partial_pair", 2, 1),
    ("original_empty_status", 0, 2),
    ("positive_witness_empty_status", 0, 2),
    ("zero_witness_empty_status", 0, 2),
])
def test_public_unresolved_v3_records_remain_portable_without_claiming_a_pair(
        client, database, make_case, kind, expected_cancelled, expected_unresolved):
    rows = round_trip()
    if kind == "unpaired_negative":
        rows = [rows[2]]
    elif kind == "partial_pair":
        rows.append(transaction(**{"交易时间": "2026-01-01 10:03:00", "交易金额": "-2.00",
                                   "交易流水号": "SYN-UNPAIRED", "交易余额": "102.00", "摘要说明": "冲正"}))
    elif kind == "original_empty_status":
        rows[1]["交易是否成功"] = ""
    else:
        rows[0]["交易是否成功"] = ""
        if kind == "zero_witness_empty_status":
            rows[0]["交易金额"] = "0.00"
    for row in rows:
        row["凭证号"] = row["交易流水号"]
    cid = make_case("Synthetic unresolved v3 package portability")
    view = preview(client, cid, source(rows, path="table.csv")[1], "synthetic.zip")
    assert view["table_summary"]["cancelled"] == expected_cancelled
    assert view["table_summary"]["unresolved"] == expected_unresolved
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    records = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()["items"]
    assert sum(record["disposition"] == "cancelled" for record in records) == expected_cancelled
    assert sum(record["disposition"] == "unresolved" for record in records) == expected_unresolved
    if "empty_status" in kind:
        empty_status_number = 2 if kind == "original_empty_status" else 1
        assert records[empty_status_number - 1]["disposition"] == "unresolved"
        assert "unknown_status" in records[empty_status_number - 1]["details"]["validation_issues"].split(",")
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert _journal(database, "import_records", restored) == _journal(database, "import_records", cid)
    facts = ",".join((*table_import.FLOW_FIELDS, "self_account", "self_name"))
    assert database.q_raw(f"select {facts} from flows where case_id=? order by trade_no,summary", [restored]) == database.q_raw(
        f"select {facts} from flows where case_id=? order by trade_no,summary", [cid])


@pytest.mark.parametrize("profile,expected_gaps,expected_flows", [
    ("funds-trace-table-v5", 2, 3),
    ("funds-trace-table-v6", 3, 2),
    ("funds-trace-table-v7", 3, 2),
])
def test_public_later_competing_original_preserves_proof_history_and_coverage_gap(
        client, database, make_case, monkeypatch, profile, expected_gaps, expected_flows):
    rows = round_trip()
    for row in rows:
        row.update({"凭证号": row["交易流水号"], "商户名称": "Synthetic competing interpretation"})
    payload, archive = source(rows, path="table.csv")
    cid = make_case("Synthetic later original interpretation")
    proof = preview(client, cid, archive, "synthetic.zip")
    assert complete(client, cid, proof)["stage"] == "done"
    with monkeypatch.context() as frozen:
        frozen.setattr(imports, "TABLE_PROFILE", profile)
        later = preview(client, cid, payload)
    revised = client.post(f"/api/v1/cases/{cid}/imports/{later['import_id']}/preview", json={
        "mode": later["mode"], "mapping": {**later["mapping"], "summary": "商户名称"}})
    assert revised.status_code == 200, revised.text
    assert complete(client, cid, revised.json())["stage"] == "done"
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid])[0]["n"] == expected_flows
    coverage = client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]
    assert coverage["unresolved_records"] == expected_gaps and not coverage["financial_interpretation_complete"]
    original_records = database.q_raw("select record_no,disposition,details_json from import_records where source_id=? order by record_no", [proof["import_id"]])
    assert [record["disposition"] for record in original_records] == ["imported", "cancelled", "cancelled"]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert client.get(f"/api/v1/cases/{restored}/stats").json()["import_coverage"] == coverage
    facts = ",".join((*table_import.FLOW_FIELDS, "self_account", "self_name"))
    for table in ("flows", "effective_flows"):
        assert database.q_raw(f"select {facts} from {table} where case_id=? order by trade_no,summary", [restored]) == database.q_raw(
            f"select {facts} from {table} where case_id=? order by trade_no,summary", [cid])
    assert database.q_raw("select parser_version,imported_at from import_sources where case_id=? order by imported_at", [restored]) == database.q_raw(
        "select parser_version,imported_at from import_sources where case_id=? order by imported_at", [cid])
    assert database.q_raw("select r.record_no,r.disposition,r.details_json from import_records r join import_sources s on s.source_id=r.source_id where r.case_id=? and s.parser_version=? order by r.record_no", [restored, V3]) == original_records


def _seed_later_negative_actual(database, cid, proof_source):
    old_plan = _plan(profile=V2)
    later_source, links = _seed_plan(database, cid, old_plan)
    fields = (*table_import.FLOW_FIELDS, "self_account", "self_name")
    flow = database.q_raw("select * from flows where id=?", [links[2]])[0]
    flow.update(trade_time="2026-01-01 10:02:00", summary="冲正")
    negative_id = database.q(f"insert into flows(case_id,{','.join(fields)}) values (?,{','.join('?' for _ in fields)}) returning id",
                             [cid, *(flow[field] for field in fields)])[0]["id"]
    notes = old_plan["records"][2]["details"].copy()
    notes.pop("validation_issues", None)
    notes.update(financial_effect="source_reported", flow_sha256=table_import.flow_digest(flow))
    database.x("update import_records set disposition='imported',reason_code='transaction_valid',details_json=? where source_id=? and record_no=3",
               [json.dumps(notes), later_source])
    database.x("insert into flow_sources(case_id,source_id,source_row,flow_id,disposition) values (?,?,3,?,'imported')", [cid, later_source, negative_id])
    timestamps = database.q_raw("select source_id,imported_at from import_sources where case_id=?", [cid])
    times = {row["source_id"]: row["imported_at"] for row in timestamps}
    assert times[later_source] > times[proof_source]
    return later_source


def test_later_negative_actual_preserves_competition_and_never_hides_its_money(client, database, make_case):
    cid = make_case("Synthetic later negative interpretation")
    proof_source, _ = _seed_plan(database, cid, _plan())
    _seed_later_negative_actual(database, cid, proof_source)
    coverage = client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]
    assert coverage["unresolved_records"] == 2 and not coverage["financial_interpretation_complete"]
    assert database.q_raw("select amount from effective_flows where case_id=? and trade_time='2026-01-01 10:02:00'", [cid]) == [{"amount": 10}]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert client.get(f"/api/v1/cases/{restored}/stats").json()["import_coverage"] == coverage
    assert database.q_raw("select amount from effective_flows where case_id=? and trade_time='2026-01-01 10:02:00'", [restored]) == [{"amount": 10}]
    assert database.q_raw("select amount,trade_time,summary from flows where case_id=? order by trade_time,summary", [restored]) == database.q_raw(
        "select amount,trade_time,summary from flows where case_id=? order by trade_time,summary", [cid])


@pytest.mark.parametrize("leg", ["original", "negative"])
def test_equal_import_time_does_not_establish_later_competition(database, make_case, leg):
    cid = make_case("Synthetic equal operational timestamps")
    proof_source, _ = _seed_plan(database, cid, _plan())
    if leg == "negative":
        later_source = _seed_later_negative_actual(database, cid, proof_source)
    else:
        later_source, links = _seed_plan(database, cid, _plan(profile=V2))
        database.x("update flows set summary='Synthetic competing facts' where id=?", [links[2]])
        flow = database.q_raw("select * from flows where id=?", [links[2]])[0]
        database.x("update import_records set details_json=json_merge_patch(details_json,?) where source_id=? and record_no=2",
                   [json.dumps({"flow_sha256": table_import.flow_digest(flow)}), later_source])
    database.x("update import_sources set imported_at=(select imported_at from import_sources where source_id=?) where source_id=?", [proof_source, later_source])
    _assert_rejected(database, _members(case_packages.export_package(cid)[0]))


def test_matching_later_original_still_binds_all_critical_facts(database, make_case):
    cid = make_case("Synthetic same later canonical facts")
    proof_source, _ = _seed_plan(database, cid, _plan())
    _seed_plan(database, cid, _plan(profile=V2))
    for number, patch in ((2, {"cancellation_amount": "9.00", "cancellation_balance": "91.00"}),
                          (3, {"cancellation_amount": "-9.00"})):
        database.x("update import_records set details_json=json_merge_patch(details_json,?) where source_id=? and record_no=?", [json.dumps(patch), proof_source, number])
    _assert_rejected(database, _members(case_packages.export_package(cid)[0]))


def test_public_failed_negative_retains_non_transaction_disposition_in_package(client, database, make_case):
    rows = round_trip() + [transaction(**{"交易时间": "2026-01-01 10:03:00", "交易金额": "-2.00",
                                         "交易是否成功": "失败", "交易流水号": "SYN-FAILED", "交易余额": "102.00"})]
    for row in rows:
        row["凭证号"] = row["交易流水号"]
    cid = make_case("Synthetic failed negative is not a cancellation candidate")
    view = preview(client, cid, source(rows, path="table.csv")[1], "synthetic.zip")
    assert view["table_summary"]["cancelled"] == 2
    assert complete(client, cid, view)["stage"] == "done"
    record = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()["items"][3]
    assert (record["disposition"], record["reason_code"]) == ("non_transaction", "failed_transaction")
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert _journal(database, "import_records", restored) == _journal(database, "import_records", cid)
