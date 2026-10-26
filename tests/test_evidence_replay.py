"""The bounded DB replay must preserve the previous list-based decisions."""
import hashlib
import json

import pytest
import duckdb

from app import evidence_resolution as e
from test_folder_evidence import accepted, account, table, transaction


def list_load(database, cid):
    # The frozen v9 supplier, retained independently as a compatibility oracle.
    query = """select m.sha256,r.source_row,r.details_json,f.flow_id,r.member_path
        from import_records r join import_sources s on s.case_id=r.case_id and s.source_id=r.source_id
        join evidence_sources b on b.case_id=r.case_id and b.source_id=r.source_id and b.active
        join import_members m on m.case_id=r.case_id and m.source_id=r.source_id and m.member_path=r.member_path
        left join flow_sources f on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no
        where r.case_id=? and s.parser_version in (?,?,?) and r.disposition in ('auxiliary','imported','duplicate')
        order by r.source_id,r.record_no"""
    cursor = database.con().execute(query, [cid, e.TABLE_PROFILE, e.ARCHIVE_PROFILE, e.FEEDBACK_PROFILE])
    result, seen, namespaces = [], set(), {}
    while batch := cursor.fetchmany(4096):
        for digest, position, details, flow_id, path in batch:
            metadata = json.loads(details)
            value = metadata.get(e.DETAIL_FIELD)
            if not value:
                continue
            data = e.validate_evidence(value)
            data["_member_path"], data["_family"] = path, metadata.get("evidence_family", "")
            key = (data["_family"], metadata.get("family_member_sha256", path), digest)
            namespaces.setdefault(key, hashlib.sha256(e._json(key).encode()).hexdigest())
            identity = (namespaces[key], position, flow_id, hashlib.sha256(value.encode()).digest())
            if identity not in seen:
                seen.add(identity)
                result.append((namespaces[key], position, flow_id, data))
    return result


def decisions(database, cid):
    result = {name: database.q_raw(f"select * from {name} where case_id=? order by all", [cid])
              for name in ("resolution_entities", "resolved_endpoints", "resolution_subject_aliases", "resolution_state")}
    result["audit"] = database.q_raw("""select r.revision,r.summary_json,r.decisions_json from resolution_state s
        join resolution_revisions r on r.case_id=s.case_id and r.revision=s.revision where s.case_id=?""", [cid])
    return result


def test_db_replay_equals_list_across_repeats_late_conflict_unscoped_and_withdrawal(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic bounded evidence replay")
    def compare():
        originals = {name: database.q_raw(f"select * from {name} where case_id=? order by all", [cid])
                     for name in ("flows", "import_records", "flow_sources")}
        with database.transaction():
            with monkeypatch.context() as legacy:
                legacy.setattr(e, "_load", lambda case_id: list_load(database, case_id))
                expected_summary = e.rebuild(cid)
            expected = decisions(database, cid)
            revision = expected_summary["revision"]
            database.x("delete from resolution_state where case_id=?", [cid])
            database.x("delete from resolution_revisions where case_id=? and revision=?", [cid, revision])
            actual_summary = e.rebuild(cid)
            assert actual_summary == expected_summary
            assert decisions(database, cid) == expected
        for name, rows in originals.items():
            assert database.q_raw(f"select * from {name} where case_id=? order by all", [cid]) == rows
    source = [("tx.csv", table("transaction", [transaction(**{"交易账号": ""})]))]
    accepted(client, cid, source)
    compare()
    accepted(client, cid, source)
    compare()
    _, auxiliary = accepted(client, cid, [("account.csv", table("account", [account()]))])
    compare()
    accepted(client, cid, [("unscoped.csv", table("transaction", [transaction(**{"交易流水号": "SYN-UNSCOPED"})]))], scope_bank=None)
    compare()
    _, conflict = accepted(client, cid, [("conflict.csv", table("account", [account(number="SYN-OTHER")]))])
    compare()
    assert client.delete(f"/api/batches/{conflict['batch_id']}", params={"case_id": cid}).status_code == 200
    compare()
    assert client.delete(f"/api/batches/{auxiliary['batch_id']}", params={"case_id": cid}).status_code == 200
    compare()


@pytest.mark.parametrize("value", [
    {"中文": "合成甲😀\"\\\n\t", "empty": None, "flags": [True, False], "ids": [9007199254740993, -2]},
    [0, -0.0, 0.1, 1e20, {"z": "x" * 100000, "a": "\u2028中文"}],
    {"nested": [{"cards": ["000000", "SYN-C"], "names": [], "parent_entity_id": None}]},
])
def test_streamed_revision_hash_is_the_exact_existing_json_byte_protocol(value):
    assert e._hash_json(value) == hashlib.sha256(e._json(value).encode("utf-8")).hexdigest()


@pytest.mark.parametrize("variant", ["wide_unused", "missing_scope", "null_scope", "duplicate_scope",
                                     "duplicate_evidence", "escaped_duplicate_scope", "false_evidence",
                                     "unicode_evidence"])
def test_replay_projection_preserves_json_values_and_last_duplicate_key(client, database, make_case, variant):
    cid = make_case("Synthetic replay metadata projection")
    accepted(client, cid, [("tx.csv", table("transaction", [transaction()]))])
    saved = database.q_raw("select source_id,record_no,details_json from import_records where case_id=? and disposition='imported'", [cid])[0]
    notes = json.loads(saved["details_json"])
    if variant == "wide_unused":
        notes["unused_audit_note"] = "Synthetic unused note " * 100
    elif variant == "missing_scope":
        notes.pop("evidence_family", None)
        notes.pop("family_member_sha256", None)
    elif variant == "null_scope":
        notes.update(evidence_family=None, family_member_sha256=None)
    elif variant == "false_evidence":
        notes[e.DETAIL_FIELD] = False
    elif variant == "unicode_evidence":
        identity = json.loads(notes[e.DETAIL_FIELD])
        for endpoint in identity["endpoints"].values():
            endpoint["name"] = '合成名😀 "引号" \\ 路径'
        notes[e.DETAIL_FIELD] = json.dumps(identity, ensure_ascii=False)
    raw = json.dumps(notes, ensure_ascii=False)
    if variant == "duplicate_scope":
        raw = '{"evidence_family":"SYN-FIRST-MUST-NOT-WIN",' + raw[1:]
    elif variant == "escaped_duplicate_scope":
        raw = '{"evidence_\\u0066amily":"SYN-FIRST-MUST-NOT-WIN",' + raw[1:]
    elif variant == "duplicate_evidence":
        raw = '{"resolution_evidence":"invalid first value",' + raw[1:]
    database.x("update import_records set details_json=? where case_id=? and source_id=? and record_no=?",
               [raw, cid, saved["source_id"], saved["record_no"]])
    assert list(e._load(cid)) == list_load(database, cid)
    assert database.q_raw("select details_json from import_records where case_id=? and source_id=? and record_no=?",
                          [cid, saved["source_id"], saved["record_no"]])[0]["details_json"] == raw


@pytest.mark.parametrize("damage", ["array", "null", "invalid_json", "invalid_identity", "duplicate_invalid_last"])
def test_replay_projection_does_not_hide_invalid_metadata(client, database, make_case, damage):
    cid = make_case("Synthetic malformed replay metadata")
    accepted(client, cid, [("tx.csv", table("transaction", [transaction()]))])
    saved = database.q_raw("select source_id,record_no,details_json from import_records where case_id=? and disposition='imported'", [cid])[0]
    if damage == "duplicate_invalid_last":
        raw = saved["details_json"][:-1] + ',"resolution_evidence":"invalid last value"}'
    else:
        raw = {"array": "[]", "null": "null", "invalid_json": "{",
               "invalid_identity": '{"resolution_evidence":{"role":"transaction"}}'}[damage]
    database.x("update import_records set details_json=? where case_id=? and source_id=? and record_no=?",
               [raw, cid, saved["source_id"], saved["record_no"]])
    for supplier in (lambda: list_load(database, cid), lambda: list(e._load(cid))):
        with pytest.raises((AttributeError, TypeError, ValueError, duckdb.Error)):
            supplier()


def test_replay_bounds_each_sorted_result_and_preserves_sparse_source_order(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic bounded evidence replay")
    for label in ("B", "A"):
        accepted(client, cid, [(label + ".csv", table("transaction", [
            transaction(**{"交易流水号": f"SYN-{label}-{index}", "交易金额": str(index + 1)})
            for index in range(7)]))])
    # Gaps and large positions must not cause empty-range iteration or overflow.
    with database.transaction():
        database.x("update import_records set record_no=record_no*100000000000 where case_id=?", [cid])
        database.x("update flow_sources set source_row=source_row*100000000000 where case_id=?", [cid])
    expected = list_load(database, cid)
    assert len(expected) == 16  # Fourteen transactions and two scope manifests.
    monkeypatch.setattr(e, "REPLAY_CHUNK", 2)
    connection = database.con()
    sizes = []

    class Cursor:
        def __init__(self, bounded):
            self.bounded = bounded

        def check(self, rows):
            if self.bounded:
                assert len(rows) <= 2, "A sorted audit result must remain bounded"
                sizes.append(len(rows))
            return rows

        def fetchall(self):
            return self.check(connection.fetchall())

        def fetchmany(self, size):
            return self.check(connection.fetchmany(size))

    class Connection:
        def execute(self, query, parameters):
            connection.execute(query, parameters)
            return Cursor(query.startswith(e._EVIDENCE_QUERY))

    monkeypatch.setattr(database, "con", lambda: Connection())
    evidence = e._load(cid)
    assert list(evidence) == expected
    assert evidence.record_count == 16 and evidence.auxiliary_count == 2
    assert sizes == [1] * 16


def test_public_replay_keeps_the_endpoint_index_within_its_memory_budget(client, database, make_case, monkeypatch):
    import sys
    materialize = e._materialize_endpoints
    sizes = []

    def bounded(projections):
        count = len(projections)
        # This is the live index at the end of actual replay, before ordinary
        # output dictionaries are serialized. Leave headroom for Python maps,
        # but exclude retaining a separate full dictionary for every endpoint.
        shallow_bytes = sys.getsizeof(projections) + sum(
            sys.getsizeof(key) + sys.getsizeof(value) for key, value in projections.items())
        assert shallow_bytes <= 256 * count
        result = materialize(projections)
        assert projections == {} and len(result) == count
        sizes.append(count)
        return result

    monkeypatch.setattr(e, "_materialize_endpoints", bounded)
    cid = make_case("Synthetic compact endpoint index")
    rows = [transaction(**{"交易流水号": f"SYN-INDEX-{index}"}) for index in range(250)]
    accepted(client, cid, [("tx.csv", table("transaction", rows))])
    assert sizes == [750]
    assert database.q_raw("select count(*) n from resolved_endpoints where case_id=?", [cid]) == [{"n": 750}]
