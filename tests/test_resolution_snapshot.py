"""Immutable resolution audits keep their bytes without a whole-case binding."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import weakref

import pytest

from app import evidence_resolution as e
from test_folder_evidence import accepted, complete, preview, table, transaction
from test_import_chunk_transactions import snapshot


@pytest.mark.parametrize("directory", ["plain", "合成 [data] 'quoted'", "synthetic?star*"])
def test_private_snapshot_bytes_and_literal_path(database, tmp_path, monkeypatch, directory):
    if os.name == "nt" and any(char in directory for char in "?*"):
        pytest.skip("Windows does not admit these filename characters")
    parent = tmp_path / directory
    parent.mkdir()
    monkeypatch.setattr(database, "DATA_DIR", parent)
    value = {"entities": [{"name": '合成😀 "引号" \\ \n\t', "null": None}],
             "endpoints": [{"number": 9007199254740993, "bool": False}] * 2000}
    expected = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    with e._decision_snapshot(value) as info:
        path = Path(info["filename"])
        assert path.read_bytes() == expected
        assert info["size"] == len(expected) and info["sha256"] == hashlib.sha256(expected).hexdigest()
        if os.name != "nt":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        actual = database.con().execute("select filename,content,size from read_text(?)", [info["source"]]).fetchall()
        assert actual == [(info["filename"], expected.decode(), len(expected))]
    assert not path.exists() and not path.parent.exists()


def test_public_resolution_releases_projection_before_native_snapshot_insert(client, database, make_case, monkeypatch):
    class Projection(list):
        pass
    compute, query, encode = e._compute, database.q_raw, e._json
    references, inserts = [], []

    def observe_compute(*args, **kwargs):
        entities, endpoints, conflicts, unresolved = compute(*args, **kwargs)
        if kwargs.get("collect_endpoints", True):
            entities, endpoints = Projection(entities), Projection(endpoints)
            references.extend((weakref.ref(entities), weakref.ref(endpoints)))
        return entities, endpoints, conflicts, unresolved

    def observe_insert(sql, params=None):
        if "from read_text($source)" in sql:
            assert len(references) == 2 and all(reference() is None for reference in references)
            assert set(params) == {"case_id", "revision", "summary", "filename", "source", "size", "sha256"}
            inserts.append(dict(params))
        return query(sql, params)

    def no_whole_snapshot(value):
        assert not isinstance(value, dict) or set(value) != {"entities", "endpoints"}
        return encode(value)

    monkeypatch.setattr(e, "_compute", observe_compute)
    monkeypatch.setattr(e, "_json", no_whole_snapshot)
    monkeypatch.setattr(database, "q_raw", observe_insert)
    cid = make_case("Synthetic streamed resolution")
    accepted(client, cid, [("tx.csv", table("transaction", [transaction()]))])
    assert len(inserts) == 1
    stored = query("select revision,summary_json,decisions_json from resolution_revisions where case_id=?", [cid])[0]
    entities = [json.loads(row["details_json"]) for row in query(
        "select details_json from resolution_entities where case_id=? order by entity_id", [cid])]
    endpoints = query("select flow_id,role,entity_id,state,reason,endpoint_account,endpoint_card,card_state from resolved_endpoints where case_id=? order by flow_id,role", [cid])
    summary = json.loads(stored["summary_json"])
    assert summary.pop("revision") == stored["revision"]
    canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert stored["decisions_json"] == canonical({"entities": entities, "endpoints": endpoints})
    assert stored["revision"] == hashlib.sha256(canonical([summary, entities, endpoints]).encode()).hexdigest()
    assert all(not Path(item["filename"]).parent.exists() for item in inserts)


@pytest.mark.parametrize("failure", ["encode", "short_write", "close", "truncate", "invalid_utf8", "missing", "digest", "state_sql"])
def test_snapshot_failure_preserves_previous_case_and_cleans_owned_files(client, database, make_case, monkeypatch, failure):
    cid = make_case("Synthetic snapshot rollback")
    accepted(client, cid, [("old.csv", table("transaction", [transaction(**{"交易流水号": "SYN-OLD"})]))])
    before = snapshot(database, cid)
    view = preview(client, cid, [("new.csv", table("transaction", [transaction(**{"交易流水号": "SYN-NEW"})]))])
    create, temporary, encode, execute = e._decision_snapshot, e.tempfile.NamedTemporaryFile, e._json_chunks, database.x
    paths = []

    @contextmanager
    def damaged(value):
        with create(value) as info:
            paths.append(Path(info["filename"]))
            if failure == "truncate":
                paths[-1].write_bytes(b"{}")
            elif failure == "invalid_utf8":
                paths[-1].write_bytes(b"\xff")
            elif failure == "missing":
                paths[-1].unlink()
            elif failure == "digest":
                info["sha256"] = "0" * 64
            yield info

    class Output:
        def __init__(self, stream):
            self.stream = stream
            paths.append(Path(stream.name))
        def __enter__(self):
            self.stream.__enter__()
            return self
        def __getattr__(self, name):
            return getattr(self.stream, name)
        def write(self, chunk):
            return self.stream.write(chunk[:-1]) if failure == "short_write" else self.stream.write(chunk)
        def __exit__(self, *args):
            self.stream.__exit__(*args)
            if failure == "close":
                raise OSError("Synthetic snapshot close failure")

    def broken_encoding(value):
        # Revision hashing occurs first; fail only the decision-file encoding.
        if failure == "encode" and isinstance(value, dict) and set(value) == {"entities", "endpoints"}:
            raise ValueError("Synthetic snapshot encoding failure")
        yield from encode(value)

    def fail_state(sql, params=None):
        if failure == "state_sql" and sql.startswith("insert into resolution_state"):
            database.con().execute("select * from synthetic_missing_snapshot_relation")
        return execute(sql, params)

    with monkeypatch.context() as patch:
        patch.setattr(e, "_decision_snapshot", damaged)
        patch.setattr(e.tempfile, "NamedTemporaryFile", lambda *a, **kw: Output(temporary(*a, **kw)))
        patch.setattr(e, "_json_chunks", broken_encoding)
        patch.setattr(database, "x", fail_state)
        result = complete(client, cid, view)
    assert result["stage"] == "error" and result["result"] is None, result
    assert snapshot(database, cid) == before
    assert paths and all(not path.exists() and not path.parent.exists() for path in paths)
    assert not list(database.DATA_DIR.glob("fundstrace-resolution-*"))


def test_repeated_revision_reuses_its_exact_immutable_snapshot(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic retained resolution audit")
    members = [("tx.csv", table("transaction", [transaction()]))]
    accepted(client, cid, members)
    before = database.q_raw("select revision,summary_json,decisions_json from resolution_revisions where case_id=?", [cid])
    assert len(before) == 1

    def unexpected_snapshot(value):
        raise AssertionError("An existing immutable revision needs no new file or wide INSERT")

    monkeypatch.setattr(e, "_decision_snapshot", unexpected_snapshot)
    _, repeated = accepted(client, cid, members)
    assert repeated["ok"] == 0
    assert database.q_raw("select revision,summary_json,decisions_json from resolution_revisions where case_id=?", [cid]) == before
    assert database.q_raw("select revision from resolution_state where case_id=?", [cid]) == [{"revision": before[0]["revision"]}]


def test_projection_write_releases_previous_endpoint_batches(client, database, make_case, monkeypatch):
    class Endpoint(dict):
        pass
    compute, insert = e._compute, database.insert_dataframe
    references, written, retained_counts = [], 0, []

    def observed_compute(*args, **kwargs):
        entities, endpoints, conflicts, unresolved = compute(*args, **kwargs)
        if kwargs.get("collect_endpoints", True):
            endpoints = [Endpoint(item) for item in endpoints]
            references.extend(weakref.ref(item) for item in endpoints)
        return entities, endpoints, conflicts, unresolved

    def bounded_insert(table_name, frame, columns):
        nonlocal written
        if table_name == "resolved_endpoints":
            retained_counts.append(sum(reference() is not None for reference in references[:written]))
            assert retained_counts[-1] == 0, "Already persisted endpoint dictionaries remain live"
            written += len(frame)
        return insert(table_name, frame, columns)

    monkeypatch.setattr(e, "WRITE_CHUNK", 2)
    monkeypatch.setattr(e, "_compute", observed_compute)
    monkeypatch.setattr(database, "insert_dataframe", bounded_insert)
    cid = make_case("Synthetic consumed endpoint projection")
    result = complete(client, cid, preview(client, cid, [("tx.csv", table("transaction", [
        transaction(**{"交易流水号": f"SYN-CONSUMED-{index}"}) for index in range(5)]))]))
    assert retained_counts and not any(retained_counts), f"Persisted endpoint dictionaries still alive: {retained_counts}"
    assert result["stage"] == "done", result.get("error")
    assert written == 15 and len(references) == 15
    assert all(reference() is None for reference in references)
    assert database.q_raw("select count(*) n from resolved_endpoints where case_id=?", [cid]) == [{"n": 15}]
