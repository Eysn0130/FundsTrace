"""Flow-sized identity state streams without changing the canonical revision."""
import hashlib
import json
from pathlib import Path
import weakref

from app import evidence_resolution as e
from app import import_staging
from app import feedback_import
from test_folder_evidence import accepted, account, complete, preview, table, transaction
from test_import_chunk_transactions import snapshot


def test_pure_replay_streams_ordered_endpoints_and_identical_json(database, monkeypatch):
    import copy
    monkeypatch.setattr(import_staging, "PLAN_CHUNK", 2)
    evidence = []
    for number, flow_id in enumerate((1, 10, 2, 9007199254740993), 1):
        supplied = transaction(**{"交易流水号": f"SYN-{number}"})
        raw = {key: supplied.get(key, "") for key in feedback_import.COLUMNS}
        row, _disposition, _reason, _details, context = feedback_import._transaction(raw, feedback_import.EVIDENCE_PROFILE)
        value = e.feedback_evidence(raw, row, context)
        evidence.append(("synthetic-scope", number, flow_id, e.validate_evidence(value)))
    expected = e._compute(copy.deepcopy(evidence))
    with e._ResolutionStaging() as staging:
        entities, endpoints, conflicts, unresolved = e._compute(copy.deepcopy(evidence), staging=staging)
        assert list.__len__(endpoints) == 0 and len(endpoints) == 12
        assert (entities, list(endpoints), conflicts, unresolved) == expected
        value = [entities, endpoints, conflicts, unresolved]
        encoded = e._json(list(expected)).encode()
        assert b"".join(e._json_chunks(value)) == encoded
        assert e._hash_json(value) == hashlib.sha256(encoded).hexdigest()
        with e._decision_snapshot({"entities": entities, "endpoints": endpoints}) as info:
            assert json.loads(Path(info["filename"]).read_text()) == {"entities": entities, "endpoints": expected[1]}
        assert staging.peak_pending <= 2
    assert staging.owner._closed


def test_private_seen_scopes_and_streamed_objects_stay_bounded(database, monkeypatch):
    monkeypatch.setattr(import_staging, "PLAN_CHUNK", 3)
    with e._ResolutionStaging() as staging:
        for _ in range(2):
            with staging.seen() as seen:
                for number in range(200):
                    identity = ("scope", number, number, hashlib.sha256(str(number).encode()).digest())
                    assert seen.admit(identity)
                    assert not seen.admit(identity)
            assert staging.connection.execute("select count(*) from resolution_seen").fetchone()[0] == 0
        for number in range(200):
            staging[number, "payer"] = ("synthetic entity", "resolved", "explicit_account")
        endpoints = staging.rows()
        assert list.__len__(endpoints) == 0 and len(endpoints) == 200
        class Endpoint(dict):
            pass
        iterate = e._EndpointRows.__iter__
        references, peaks = [], []
        def observed(self):
            for row in iterate(self):
                current = Endpoint(row)
                references.append(weakref.ref(current))
                peaks.append(sum(reference() is not None for reference in references))
                yield current
        monkeypatch.setattr(e._EndpointRows, "__iter__", observed)
        e._hash_json({"endpoints": endpoints})
        with e._decision_snapshot({"endpoints": endpoints}) as info:
            assert info["size"] > 0
        assert max(peaks) <= 2 and all(reference() is None for reference in references)
        assert staging.peak_pending <= 3
    assert staging.owner._closed


def assert_same_projection(database, cid):
    evidence = e._load(cid)
    entities, endpoints, conflicts, unresolved = e._compute(evidence)
    with e._ResolutionStaging() as staging:
        staged_entities, staged_endpoints, staged_conflicts, staged_unresolved = e._compute(e._load(cid), staging=staging)
        assert isinstance(staged_endpoints, e._EndpointRows)
        assert list.__len__(staged_endpoints) == 0
        assert list(staged_endpoints) == endpoints
        assert (staged_entities, staged_conflicts, staged_unresolved) == (entities, conflicts, unresolved)
        expected = {"entities": entities, "endpoints": endpoints}
        actual = {"entities": staged_entities, "endpoints": staged_endpoints}
        encoded = json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        assert b"".join(e._json_chunks(actual)) == encoded
        assert e._hash_json(actual) == hashlib.sha256(encoded).hexdigest()
        with e._decision_snapshot(actual) as info:
            assert Path(info["filename"]).read_bytes() == encoded
        assert not staging.connection.execute("select count(*) from resolution_seen").fetchone()[0]
    assert staging.owner._closed
    stored = database.q_raw("""select r.revision,r.summary_json,r.decisions_json from resolution_revisions r
        join resolution_state s on s.case_id=r.case_id and s.revision=r.revision where r.case_id=?""", [cid])[0]
    summary = json.loads(stored["summary_json"])
    assert summary.pop("revision") == stored["revision"]
    assert stored["decisions_json"] == encoded.decode()
    assert stored["revision"] == e._hash_json([summary, entities, endpoints])


def test_public_late_declarations_conflicts_deletion_and_restore_match_original_replay(client, database, make_case):
    cid = make_case("Synthetic disk identity replay")
    accepted(client, cid, [("transactions.csv", table("transaction", [transaction(**{"交易账号": ""}),
        transaction(**{"交易账号": "", "交易流水号": "SYN-SECOND", "交易金额": "4.01"})]))])
    assert_same_projection(database, cid)
    _, declaration = accepted(client, cid, [("accounts.csv", table("account", [account()]))])
    assert_same_projection(database, cid)
    _, conflicting = accepted(client, cid, [("contradiction.csv", table("account", [account(number="SYN-OTHER")]))])
    assert_same_projection(database, cid)
    assert client.delete(f"/api/batches/{conflicting['batch_id']}", params={"case_id": cid}).status_code == 200
    assert_same_projection(database, cid)
    assert client.delete(f"/api/batches/{declaration['batch_id']}", params={"case_id": cid}).status_code == 200
    assert_same_projection(database, cid)
    exported = client.get(f"/api/cases/{cid}/export")
    assert exported.status_code == 200
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", exported.content)})
    assert restored.status_code == 200, restored.text
    assert_same_projection(database, restored.json()["case_id"])


def test_endpoint_objects_do_not_accumulate_during_hash_audit_or_writes(client, database, make_case, monkeypatch):
    class Endpoint(dict):
        pass
    iterate = e._EndpointRows.__iter__
    references, peaks, owners = [], [], []
    enter = e._ResolutionStaging.__enter__
    def entered(self):
        owners.append(self)
        return enter(self)
    def observed(self):
        assert list.__len__(self) == 0
        for item in iterate(self):
            value = Endpoint(item)
            references.append(weakref.ref(value))
            peaks.append(sum(reference() is not None for reference in references))
            yield value
    monkeypatch.setattr(e._EndpointRows, "__iter__", observed)
    monkeypatch.setattr(e._ResolutionStaging, "__enter__", entered)
    monkeypatch.setattr(e, "WRITE_CHUNK", 7)
    monkeypatch.setattr(import_staging, "PLAN_CHUNK", 5)
    cid = make_case("Synthetic bounded endpoints")
    rows = [transaction(**{"交易流水号": f"SYN-{number}"}) for number in range(80)]
    _, result = accepted(client, cid, [("rows.csv", table("transaction", rows))])
    assert result["ok"] == 80
    assert database.q_raw("select count(*) n from resolved_endpoints where case_id=?", [cid])[0]["n"] == 240
    assert references and max(peaks) <= 2
    assert all(reference() is None for reference in references)
    assert owners and all(owner.owner._closed and owner.peak_pending <= 5 for owner in owners)
    assert all(owner.seen_inserts > 0 for owner in owners)


def test_private_endpoint_write_failure_rolls_back_case_and_closes_store(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic identity staging rollback")
    accepted(client, cid, [("old.csv", table("transaction", [transaction()]))])
    before = snapshot(database, cid)
    view = preview(client, cid, [("new.csv", table("transaction", [transaction(**{"交易流水号": "SYN-NEW"})]))])
    owners = []
    original = e._ResolutionStaging.flush
    def fail(self):
        owners.append(self)
        original(self)
        raise OSError("Synthetic private endpoint write failure")
    monkeypatch.setattr(e._ResolutionStaging, "flush", fail)
    result = complete(client, cid, view)
    assert result["stage"] == "error" and result["done"] == 0
    assert snapshot(database, cid) == before
    assert owners and all(owner.owner._closed for owner in owners)
