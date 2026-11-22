"""Public replay against the actual main 0335d7e single-tier interpretation."""
from hashlib import sha256
import json
import uuid

import pytest

from app import import_preview_cache
from test_feedback_import_api import complete
from test_feedback_package import _archive, _members, _mutate


FILENAME = "synthetic-tiered-replay.csv"
CONTENT = (
    "交易日期,payer_account,payee_account,amount,balance,unused,unused_two\r\n"
    "2026-01-01,SYN-A,SYN-B,2.00,95.00,交易,本笔发生额\r\n"
    "入账,收款方,付款方,账面余额,本笔发生额,交易,本笔发生额复核\r\n"
    "年月日,账户标识,账户标识,元,分,年月日,分\r\n"
    "2026-01-20,SYN-REC,SYN-PAY,100.00,301,2026-01-07,301\r\n"
).encode()
SOURCE_SHA = "67b446f47d0536e484a21fb7ea730b4b512862dcda6438cdd74205bd66bca10a"
MEMBER_PATH = "table.csv"

# Captured via main's real public preview, repreview and commit of these bytes.
OLD_PROFILE = "funds-trace-table-v69"
OLD_REQUEST = {"_table_profile": OLD_PROFILE}
OLD_COLUMNS = [
    "交易日期", "payer_account", "payee_account", "amount", "balance", "unused", "unused_two",
]
OLD_MAPPING = {
    "trade_time": "交易日期", "payer_account": "payer_account", "payee_account": "payee_account",
    "amount": "amount", "balance": "balance",
}
OLD_FACTS = {
    ("2026-01-01 00:00:00", "SYN-A", "SYN-B", "2.00", "95.00", "CNY"),
    ("2026-01-20 00:00:00", "SYN-REC", "SYN-PAY", "100.00", "301.00", "CNY"),
}
OLD_JOURNAL = [
    (1, 2, "imported", "transaction_valid"),
    (2, 3, "unresolved", "invalid_time"),
    (3, 4, "unresolved", "invalid_time"),
    (4, 5, "imported", "transaction_valid"),
]
NEW_PROFILE = "funds-trace-table-v88"
NEW_FACTS = {
    ("2026-01-01 00:00:00", "SYN-A", "SYN-B", "2.00", "95.00", "CNY"),
    ("2026-01-07 00:00:00", "SYN-PAY", "SYN-REC", "3.01", "100.00", "CNY"),
}


def _case(case_id):
    return f"/api/v1/cases/{case_id}"


def _ok(response):
    assert response.is_success, response.text
    return response.json()


def _facts(rows):
    fields = ("trade_time", "payer_account", "payee_account", "amount", "balance", "currency")
    return {tuple(row[field] for field in fields) for row in rows}


def _journal(records):
    fields = ("record_no", "source_row", "disposition", "reason_code")
    return [tuple(row[field] for field in fields) for row in records]


def _cold_cache():
    import_preview_cache.discard()
    assert import_preview_cache._slot is None


def _old_ready_session(database, case_id):
    # Recreate the frozen old DB projection and its retained bytes without
    # invoking any candidate parser or upload defaults to build the fixture.
    assert sha256(CONTENT).hexdigest() == SOURCE_SHA
    import_id = uuid.uuid4().hex
    uploads = database.DATA_DIR / "uploads"
    uploads.mkdir(exist_ok=True)
    (uploads / (import_id + ".csv")).write_bytes(CONTENT)
    frozen_json = [json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                   for value in (OLD_MAPPING, OLD_COLUMNS, OLD_REQUEST)]
    database.x("""insert into import_sessions
        (import_id, case_id, surface, filename, suffix, sha256, size_bytes, mode, mapping_json,
         columns_json, request_json, stage, attempt, source_state, expires_at)
        values (?, ?, 'v1', ?, '.csv', ?, ?, 'A', ?, ?, ?, 'ready', 0, 'retained',
                current_timestamp + interval '7 days')""",
        [import_id, case_id, FILENAME, SOURCE_SHA, len(CONTENT), *frozen_json])
    return import_id


def _export(client, case_id):
    response = client.get(f"/api/cases/{case_id}/export")
    assert response.is_success, response.text
    return response.content


def _restore(client, content):
    return _ok(client.post("/api/cases/import", files={"file": ("synthetic-replay.fundcase", content)}))["case_id"]


def _assert_old_summary(summary):
    assert summary["profile"] == OLD_PROFILE
    assert (summary["record_total"], summary["accepted"], summary["unresolved"]) == (4, 2, 2)
    assert summary["currency_totals"] == {"CNY": "102.00"}
    assert summary["reason_counts"] == {"transaction_valid": 2, "invalid_time": 2}


def test_frozen_main_ready_session_replays_old_facts_and_restores_its_package(client, database, make_case):
    case_id = make_case("Synthetic old ready tiered replay")
    import_id = _old_ready_session(database, case_id)
    base = _case(case_id) + "/imports/" + import_id
    _cold_cache()
    view = _ok(client.post(base + "/preview", json={"mode": "A", "mapping": OLD_MAPPING}))
    assert view["import_id"] == import_id
    assert (view["mode"], view["mapping"], view["columns"]) == ("A", OLD_MAPPING, OLD_COLUMNS)
    _assert_old_summary(view["table_summary"])
    assert _facts(view["preview"]) == OLD_FACTS
    assert [(row["source_member"], row["source_row"]) for row in view["preview"]] == [(MEMBER_PATH, 2), (MEMBER_PATH, 5)]
    _cold_cache()
    completed = complete(client, case_id, view)
    assert completed["stage"] == "done", completed
    assert completed["result"]["ok"] == 2
    _assert_old_summary(completed["result"]["table_summary"])
    assert _facts(_ok(client.get(_case(case_id) + "/flows"))["items"]) == OLD_FACTS
    assert _journal(_ok(client.get(base + "/records"))["items"]) == OLD_JOURNAL
    assert client.get(_case(case_id) + f"/sources/{import_id}/file").content == CONTENT
    source, = database.q_raw("select parser_version,sha256 from import_sources where source_id=?", [import_id])
    assert source == {"parser_version": OLD_PROFILE, "sha256": SOURCE_SHA}

    restored_id = _restore(client, _export(client, case_id))
    assert _facts(_ok(client.get(_case(restored_id) + "/flows"))["items"]) == OLD_FACTS
    restored, = database.q_raw("select source_id,parser_version,sha256,origin from import_sources where case_id=?", [restored_id])
    assert (restored["parser_version"], restored["sha256"], restored["origin"]) == (OLD_PROFILE, SOURCE_SHA, "package")
    restored_base = _case(restored_id) + "/imports/" + restored["source_id"]
    assert _journal(_ok(client.get(restored_base + "/records"))["items"]) == OLD_JOURNAL
    assert client.get(_case(restored_id) + f"/sources/{restored['source_id']}/file").status_code == 404


@pytest.mark.parametrize("patch", [
    {"_table_profile": "funds-trace-table-v999"},
    {"_header_plan": {"header_rows": [3, 4], "columns": [{"column": 2, "role": "payer_account"}]}},
], ids=["unknown-parser-profile", "private-historical-model-plan"])
def test_invalid_session_replay_pin_is_rejected_before_financial_writes(client, database, make_case, patch):
    case_id = make_case("Synthetic invalid tiered replay pin")
    import_id = _old_ready_session(database, case_id)
    database.x("update import_sessions set request_json=? where import_id=?",
               [json.dumps({**OLD_REQUEST, **patch}), import_id])
    _cold_cache()
    base = _case(case_id) + "/imports/" + import_id
    body = {"mode": "A", "mapping": OLD_MAPPING}
    for action in ("preview", "commit"):
        response = client.post(base + "/" + action, json=body)
        assert response.status_code == 400, (action, response.status_code, response.text)
        assert response.json()["error"]["code"] == "invalid_table_file"
    assert database.q_raw("select stage,attempt from import_sessions where import_id=?", [import_id]) == [
        {"stage": "ready", "attempt": 0}]
    assert _ok(client.get(_case(case_id) + "/flows"))["total"] == 0
    for table in ("batches", "import_sources", "import_members", "import_records", "flow_sources"):
        assert database.q_raw(f"select count(*) n from {table} where case_id=?", [case_id]) == [{"n": 0}]


def test_new_public_session_replays_exact_tiers_and_rejects_mismatched_package_proofs(
        client, database, make_case, tmp_path):
    case_id = make_case("Synthetic cold tiered replay")
    imports = _case(case_id) + "/imports"
    view = _ok(client.post(imports + "/preview", files={"file": (FILENAME, CONTENT, "text/csv")}))
    summary = view["table_summary"]
    assert summary["profile"] == NEW_PROFILE
    assert summary["currency_totals"] == {"CNY": "5.01"}
    assert (summary["accepted"], summary["unresolved"]) == (2, 0)
    assert _facts(view["preview"]) == NEW_FACTS
    assert [(row["source_member"], row["source_row"]) for row in view["preview"]] == [(MEMBER_PATH, 2), (MEMBER_PATH, 5)]
    import_id = view["import_id"]
    base = imports + "/" + import_id
    request = {"mode": view["mode"], "mapping": view["mapping"]}
    frozen, = database.q_raw("select sha256,request_json from import_sessions where import_id=?", [import_id])
    pin = json.loads(frozen["request_json"])
    assert frozen["sha256"] == SOURCE_SHA and pin["_table_profile"] == NEW_PROFILE
    assert "_header_plan" not in pin

    _cold_cache()
    rechecked = _ok(client.post(base + "/preview", json=request))
    assert rechecked["table_summary"] == summary
    assert rechecked["preview"] == view["preview"]
    _cold_cache()
    job = complete(client, case_id, rechecked)
    assert job["stage"] == "done", job
    assert job["result"]["ok"] == 2
    assert _facts(_ok(client.get(_case(case_id) + "/flows"))["items"]) == NEW_FACTS
    coverage = _ok(client.get(_case(case_id) + "/stats"))["import_coverage"]
    assert coverage["unresolved_records"] == 0 and coverage["financial_interpretation_complete"]
    assert client.get(_case(case_id) + f"/sources/{import_id}/file").content == CONTENT
    records = _ok(client.get(base + "/records"))["items"]
    assert [(row["source_row"], row["member_path"]) for row in records] == [
        (position, MEMBER_PATH) for position in (2, 3, 4, 5)]
    direct, parent, leaf, transaction = records
    assert (direct["disposition"], leaf["reason_code"], transaction["disposition"]) == (
        "imported", "repeated_header", "imported")
    assert (parent["disposition"], parent["reason_code"], parent["flow_id"]) == (
        "non_transaction", "tiered_header_parent", None)
    declared_layout = parent["details"]["tiered_header_evidence"]
    proof = json.loads(declared_layout)
    assert (proof["version"], proof["parent_row"], proof["width"], proof["status"]) == (1, 3, 7, "verified")
    columns = {column[0]: column for column in proof["columns"]}
    assert columns[2][1:4] == ["收款方", "账户标识", "payee_account"]
    assert columns[3][1:4] == ["付款方", "账户标识", "payer_account"]
    assert columns[5] == [5, "本笔发生额", "分", "amount", "分"]
    assert columns[7] == [7, "本笔发生额复核", "分", "amount", "分"]
    assert transaction["details"]["tiered_header_binding"] == sha256(declared_layout.encode()).hexdigest()
    assert "tiered_header_binding" not in direct["details"]

    review = _ok(client.get(base + "/review", params={"member_path": MEMBER_PATH, "view": "accepted"}))
    assert review["original_error"] is None
    original = next(row["original"] for row in review["items"] if row["source_row"] == 5)
    originals = {cell["column"]: cell for cell in original}
    for column, value, role in ((2, "SYN-REC", "payee_account"), (3, "SYN-PAY", "payer_account"),
                                (5, "301", "amount"), (7, "301", "amount")):
        assert (originals[column]["value"], originals[column]["fields"]) == (value, [role])

    package = _export(client, case_id)
    restored_id = _restore(client, package)
    assert _facts(_ok(client.get(_case(restored_id) + "/flows"))["items"]) == NEW_FACTS
    restored, = database.q_raw("select source_id,parser_version,sha256 from import_sources where case_id=?", [restored_id])
    assert (restored["parser_version"], restored["sha256"]) == (NEW_PROFILE, SOURCE_SHA)
    restored_records = _ok(client.get(_case(restored_id) + f"/imports/{restored['source_id']}/records"))["items"]
    assert _journal(restored_records) == _journal(records)
    assert [row["details"] for row in restored_records] == [row["details"] for row in records]

    # Both unit labels and both declared units change to a different plausible
    # interpretation, while the bound row and the stored funds stay original.
    for position in (5, 7):
        columns[position][2] = columns[position][4] = "元"
    members = _members(package)
    different_layout = json.dumps(proof, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    wrong_position = json.dumps(
        {**json.loads(declared_layout), "parent_row": 2}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    tables = ("cases", "flows", "batches", "import_sources", "import_members", "import_records", "flow_sources")
    counts = [database.q_raw(f"select count(*) n from {table}") for table in tables]
    for source_row, changed, label in (
            (3, {"tiered_header_evidence": different_layout}, "units"),
            (3, {"tiered_header_evidence": wrong_position}, "position"),
            (5, {"tiered_header_binding": None}, "binding")):
        damaged = dict(members)
        encoded = json.dumps(changed, ensure_ascii=False).replace("'", "''")
        _mutate(damaged, "import_records", "update entries set "
                f"details_json=json_merge_patch(details_json,'{encoded}') where source_row={source_row}", tmp_path)
        response = client.post("/api/cases/import", files={"file": (f"synthetic-wrong-{label}.fundcase", _archive(damaged))})
        assert response.status_code == 400, (label, response.text)
        assert [database.q_raw(f"select count(*) n from {table}") for table in tables] == counts


def test_new_header_coverage_does_not_repair_an_old_admitted_conflicting_flow(client, database, make_case):
    case_id = make_case("Synthetic conflicting interpretations of identical bytes")
    imports = _case(case_id) + "/imports"
    old_id = _old_ready_session(database, case_id)
    old_view = _ok(client.post(imports + f"/{old_id}/preview", json={"mode": "A", "mapping": OLD_MAPPING}))
    old = complete(client, case_id, old_view)
    assert old["stage"] == "done", old
    assert _facts(_ok(client.get(_case(case_id) + "/flows"))["items"]) == OLD_FACTS

    new = _ok(client.post(imports + "/preview", files={"file": (FILENAME, CONTENT, "text/csv")}))
    assert new["table_summary"]["profile"] == NEW_PROFILE
    assert [(item["source_row"], item["reason_code"]) for item in new["table_summary"]["unresolved_examples"]] == [
        (5, "conflicting_field")]
    checked = complete(client, case_id, new)
    assert checked["stage"] == "done", checked
    assert (checked["result"]["ok"], checked["result"]["rows_dup"]) == (0, 1)
    assert _facts(_ok(client.get(_case(case_id) + "/flows"))["items"]) == OLD_FACTS
    assert _ok(client.get(imports + f"/{old_id}"))["result"] == old["result"]
    coverage = _ok(client.get(_case(case_id) + "/stats"))["import_coverage"]
    assert coverage["unresolved_records"] == 1
    assert coverage["financial_interpretation_complete"] is False
