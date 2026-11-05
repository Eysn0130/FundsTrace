"""A rebuild may reuse only individually admitted, bounded raw strings."""
from collections import Counter
import json

import pytest

from app import evidence_resolution as evidence
from test_folder_evidence import accepted, complete, preview, table, transaction


@pytest.mark.parametrize("unique", [1, 140])
def test_public_rebuild_bounds_raw_values_and_decodes_fresh_objects(client, database, make_case, monkeypatch, unique):
    load, validate, iterate = evidence._load, evidence.validate_evidence, evidence._CaseEvidence._iterate
    readers, admissions, sizes, transactions, mappings = [], [], [], [], []

    def capture(case_id):
        reader = load(case_id)
        assert reader._validated_values is None
        readers.append(reader)
        admissions.append(Counter())
        return reader

    def admitted(value):
        if readers and readers[-1]._validated_values is not None:
            admissions[-1][value] += 1
        return validate(value)

    def observed(reader, auxiliary):
        for item in iterate(reader, auxiliary):
            if not any(value is reader._validated_values for value in mappings):
                mappings.append(reader._validated_values)
            sizes.append(len(reader._validated_values))
            data = item[-1]
            assert "_source_scope" not in data and "_source_scope_conflict" not in data
            if data["role"] == "transaction":
                assert data["endpoints"]["self"]["bank"] == ""
                transactions.append(1)
            yield item

    monkeypatch.setattr(evidence, "_load", capture)
    monkeypatch.setattr(evidence, "validate_evidence", admitted)
    monkeypatch.setattr(evidence._CaseEvidence, "_iterate", observed)
    members = [("tx.csv", table("transaction", [transaction(**{
        "交易流水号": f"SYN-{i}", "对手户名": f"Synthetic counter {i % unique}"})
        for i in range(max(unique, 3))]))]
    for name in ("Synthetic admission cache", "Synthetic separate admission cache"):
        cid = make_case(name)
        accepted(client, cid, members)
        assert readers[-1]._validated_values is None
        assert admissions[-1]
        response = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()
        assert response["total"] == max(unique, 3)
    assert max(sizes) <= 128
    assert all(not value for value in mappings)
    assert admissions[0] == admissions[1]  # A new case/rebuild fully admits anew.
    if unique == 1:
        assert all(count == 1 for count in admissions[0].values())
        assert sum(admissions[0].values()) < len(transactions) // 2
    else:
        assert max(sizes) == 128 and sum(admissions[0].values()) >= unique


@pytest.mark.parametrize("first_pass", ["auxiliary", "partial", "complete"])
def test_partial_or_complete_replay_never_trusts_changed_bytes_and_rolls_back(client, database, make_case, monkeypatch, first_pass):
    cid = make_case("Synthetic changed evidence bytes")
    accepted(client, cid, [("tx.csv", table("transaction", [transaction()]))])
    tables = ("flows", "import_sources", "import_records", "flow_sources", "import_source_operations",
              "batches", "resolution_entities", "resolved_endpoints", "resolution_state")
    def state():
        return {name: database.q_raw(f"select * from {name} where case_id=? order by all", [cid]) for name in tables}
    before = state()
    view = preview(client, cid, [("extra.csv", table("transaction", [transaction(**{"交易流水号": "SYN-EXTRA"})]))])
    compute, readers, mappings = evidence._compute, [], []

    def changed(reader, **options):
        readers.append(reader)
        mappings.append(reader._validated_values)
        entries = reader.auxiliary() if first_pass == "auxiliary" else iter(reader)
        if first_pass == "complete":
            list(entries)
        else:
            next(entries)
            entries.close()
        saved = database.q_raw("select source_id,record_no,details_json from import_records where case_id=? and disposition='imported' order by source_id,record_no limit 1", [cid])[0]
        notes = json.loads(saved["details_json"])
        data = json.loads(notes[evidence.DETAIL_FIELD])
        data["endpoints"]["self"]["account"] = 123  # Closed admission rejects numeric identifiers.
        notes[evidence.DETAIL_FIELD] = json.dumps(data)
        database.x("update import_records set details_json=? where case_id=? and source_id=? and record_no=?",
                   [json.dumps(notes), cid, saved["source_id"], saved["record_no"]])
        return compute(reader, **options)

    monkeypatch.setattr(evidence, "_compute", changed)
    job = complete(client, cid, view)
    assert job["stage"] == "error"
    assert readers and all(reader._validated_values is None for reader in readers)
    assert all(not value for value in mappings)
    assert state() == before
