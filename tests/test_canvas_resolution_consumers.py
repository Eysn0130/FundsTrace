"""Canvas consumers keep typed keys while accepting scoped display identifiers."""
import io
import json
import threading
from contextlib import contextmanager
from decimal import Decimal

import openpyxl
import pytest

from app import evidence_resolution, main


def entity(database, case_id, key, account, display, *, cards=()):
    details = {"entity_id": key, "kind": "account", "account_number": account,
               "display_account": display, "bank": "Synthetic bank", "cards": list(cards),
               "parent_entity_id": None, "names": ["Synthetic shared name"], "status": "resolved"}
    database.x("""insert into resolution_entities(case_id,entity_id,kind,account_number,
                  display_account,bank,details_json) values (?,?,?,?,?,?,?)""",
               [case_id, key, "account", account, display, "Synthetic bank", json.dumps(details)])


def revision(database, case_id):
    value = "a" * 64
    summary = {"policy": evidence_resolution.POLICY, "revision": value, "entities": 3,
               "resolved_endpoints": 0, "unresolved_endpoints": 0, "conflicts": 0,
               "auxiliary_records": 0, "evidence_records": 0, "note": "Synthetic fixture"}
    database.x("insert into resolution_revisions(case_id,revision,summary_json,decisions_json) values (?,?,?,'{}')",
               [case_id, value, json.dumps(summary)])
    database.x("insert into resolution_state(case_id,revision) values (?,?)", [case_id, value])
    return value


def flow(database, case_id, payer, payee, *, self_account=None, when="2026-01-01 10:00:00", canvas_id=0):
    # Raw source identifiers remain separate from the fixture's projected keys.
    raw_payer, raw_payee = "RAW-" + payer, "RAW-" + payee
    self_account = self_account or payer
    identifier = database.q_raw("""insert into flows(case_id,trade_no,trade_time,payer_name,payer_account,
                  payee_name,payee_account,amount,currency,self_account)
                  values (?,'SYN-FLOW',?,'',?,'',?,?,'CNY',?) returning id""",
                  [case_id, when, raw_payer, raw_payee, Decimal("10.25"), "RAW-" + self_account])[0]["id"]
    for role, key in (("payer", payer), ("payee", payee), ("self", self_account)):
        database.x("""insert into resolved_endpoints(case_id,flow_id,role,entity_id,state,reason)
                      values (?,?,?,?,'resolved','synthetic')""", [case_id, identifier, role, key])
    database.x("insert into tracked_flows(case_id,flow_id,canvas_id) values (?,?,?)", [case_id, identifier, canvas_id])
    return identifier


def request(client, case_id, endpoint="paths", **values):
    response = client.post(f"/api/canvas/investigation/{endpoint}", json={"case_id": case_id, **values})
    assert response.status_code == 200, response.text
    return response.json()


def test_display_account_card_alias_and_internal_key_produce_same_paths(client, database, make_case):
    cid = make_case("Synthetic resolution paths")
    keys = ["fte_" + digit * 32 for digit in "abc"]
    for key, suffix in zip(keys, "ABC"):
        entity(database, cid, key, "SYN-ACCOUNT-" + suffix, "SYN-CARD-" + suffix,
               cards=["SYN-CARD-" + suffix, "SYN-OLDER-" + suffix])
    current_revision = revision(database, cid)
    flow(database, cid, keys[0], keys[1])
    flow(database, cid, keys[1], keys[2], when="2026-01-01 10:00:01")
    internal = request(client, cid, start_account=keys[0], end_account=keys[2])
    aliases = request(client, cid, start_account="SYN-OLDER-A", end_account="SYN-ACCOUNT-C")
    assert internal["paths"] == aliases["paths"]
    assert aliases["start_accounts"] == [keys[0]] and aliases["end_accounts"] == [keys[2]]
    assert aliases["start_display_accounts"] == ["SYN-CARD-A"]
    assert aliases["paths"][0]["accounts"] == keys
    assert aliases["paths"][0]["display_accounts"] == ["SYN-CARD-A", "SYN-CARD-B", "SYN-CARD-C"]
    assert aliases["paths"][0]["steps"][0]["source_display_account"] == "SYN-CARD-A"
    assert aliases["paths"][0]["steps"][0]["target_display_account"] == "SYN-CARD-B"
    assert aliases["statistics_policy"] == evidence_resolution.POLICY
    assert aliases["resolution_revision"] == current_revision
    assert aliases["flows"][0]["payer_display_account"] == "SYN-CARD-A"
    assert aliases["flows"][0]["self_display_account"] == "SYN-CARD-A"


def test_nonunique_alias_expands_all_in_scope_entities_without_name_or_case_guess(client, database, make_case):
    cid, other = make_case("Synthetic ambiguous alias"), make_case("Synthetic isolated aliases")
    a1, a2, target, hidden, foreign = ["fte_" + digit * 32 for digit in "abcde"]
    for key, suffix in ((a1, "A1"), (a2, "A2"), (hidden, "HIDDEN")):
        entity(database, cid, key, "SYN-ACCOUNT-" + suffix, "SHARED-CARD", cards=["SHARED-CARD"])
    entity(database, cid, target, "SYN-TARGET", "TARGET-CARD")
    entity(database, other, foreign, "SYN-FOREIGN", "SHARED-CARD", cards=["SHARED-CARD"])
    revision(database, cid)
    flow(database, cid, a1, target)
    flow(database, cid, a2, target, when="2026-01-01 11:00:00")
    # Registry metadata outside the loaded canvas must not create a path seed.
    other_canvas = database.q_raw("insert into canvases(case_id,name,snapshot) values (?,'Synthetic hidden','{}') returning id", [cid])[0]["id"]
    flow(database, cid, hidden, target, canvas_id=other_canvas)
    flow(database, other, foreign, foreign)
    result = request(client, cid, start_account="SHARED-CARD", end_account="TARGET-CARD")
    assert result["start_accounts"] == [a1, a2]
    assert {path["accounts"][0] for path in result["paths"]} == {a1, a2}
    assert hidden not in result["start_accounts"] and foreign not in result["start_accounts"]
    visible = request(client, cid, start_account="SHARED-CARD", end_account="TARGET-CARD", scope="visible",
                      visible_accounts=[a1, target], visible_edges=[{"source": a1, "target": target}])
    assert visible["start_accounts"] == [a1] and len(visible["paths"]) == 1
    window = request(client, cid, start_account="SHARED-CARD", end_account="TARGET-CARD",
                     end="2026-01-01 10:30:00")
    assert window["start_accounts"] == [a1] and len(window["paths"]) == 1
    absent = request(client, cid, start_account="SYN-FOREIGN", end_account="TARGET-CARD")
    assert absent["start_accounts"] == [] and absent["paths"] == []
    named = request(client, cid, start_account="Synthetic shared name", end_account="TARGET-CARD")
    assert named["start_accounts"] == [] and named["paths"] == []


def test_time_uses_ledger_record_policy_and_display_metadata_without_fifo_claim(client, database, make_case):
    cid = make_case("Synthetic resolution time")
    payer, payee = "fte_" + "a" * 32, "fte_" + "b" * 32
    entity(database, cid, payer, "SYN-A", "SYN-CARD-A", cards=["SYN-CARD-A"])
    entity(database, cid, payee, "SYN-B", "SYN-CARD-B", cards=["SYN-CARD-B"])
    current_revision = revision(database, cid)
    flow(database, cid, payer, payee)
    flow(database, cid, payer, payee, self_account=payee, when="2026-01-01 10:00:01")
    result = request(client, cid, endpoint="time")
    assert result["summary"]["selected_count"] == 2 and result["summary"]["mirror_duplicate_count"] == 0
    assert result["summary"]["amount_totals"] == [{"currency": "CNY", "amount": "20.50"}]
    assert result["statistics_policy"] == evidence_resolution.POLICY
    assert result["resolution_revision"] == current_revision
    assert "accepted_ledger_records" in result["semantics"] and "graph_fifo_mirror_pairing" not in result["semantics"]
    assert {node["display_account"] for node in result["nodes"]} == {"SYN-CARD-A", "SYN-CARD-B"}
    assert result["edges"][0]["source_display_account"] == "SYN-CARD-A"
    assert result["flows"][0]["source"] == payer and result["flows"][0]["source_display_account"] == "SYN-CARD-A"


def test_legacy_time_semantics_keep_its_existing_fifo_policy(client, database, make_case):
    cid = make_case("Synthetic legacy time policy")
    database.x("""insert into flows(case_id,trade_no,trade_time,payer_account,payee_account,amount,currency,self_account)
                  values (?,'SYN-LEGACY','2026-01-01 10:00:00','SYN-A','SYN-B',10.25,'CNY','SYN-A')""", [cid])
    database.x("insert into tracked_flows(case_id,flow_id,canvas_id) select case_id,id,0 from flows where case_id=?", [cid])
    result = request(client, cid, endpoint="time")
    assert result["statistics_policy"] == "legacy_mirror_fifo" and result["resolution_revision"] is None
    assert "graph_fifo_mirror_pairing" in result["semantics"]


@contextmanager
def publish_at_read_boundary(database, mutate):
    """Commit on a real second thread only where the reader releases its lock."""
    reached, attempted, committed = threading.Event(), threading.Event(), threading.Event()
    errors = []

    def writer():
        try:
            assert reached.wait(5), "reader never reached the publication boundary"
            attempted.set()
            with database.transaction():
                mutate()
        except BaseException as error:
            errors.append(error)
        finally:
            committed.set()

    thread = threading.Thread(target=writer, name="synthetic-resolution-publisher", daemon=True)
    thread.start()

    def boundary():
        reached.set()
        assert attempted.wait(5), "writer did not attempt its transaction"
        # q/q_raw have released their own lock here. An endpoint-wide lock must
        # keep the writer queued; without it the allowed interleaving commits
        # before the endpoint starts its next real read.
        if not database._lock._is_owned():
            assert committed.wait(5), "unlocked publication did not complete"

    try:
        yield boundary
    finally:
        reached.set()
        thread.join(5)
        assert not thread.is_alive(), "publication thread did not finish"
        assert not errors, errors


def publish_revision(database, case_id, value):
    current = evidence_resolution.summary(case_id)
    summary = {**current, "revision": value, "note": "Synthetic concurrent publication"}
    database.x("insert into resolution_revisions(case_id,revision,summary_json,decisions_json) values (?,?,?,'{}')",
               [case_id, value, json.dumps(summary)])
    database.x("update resolution_state set revision=? where case_id=?", [value, case_id])


@pytest.mark.parametrize("read_boundary", ["count", "rows"])
def test_public_flow_query_keeps_count_rows_and_resolution_in_one_read(
        client, database, make_case, monkeypatch, read_boundary):
    cid = make_case("Synthetic coherent flow publication")
    payer, payee, successor = ["fte_" + digit * 32 for digit in "abc"]
    for key, suffix in ((payer, "A"), (payee, "B"), (successor, "C")):
        entity(database, cid, key, "SYN-ACCOUNT-" + suffix, "SYN-CARD-" + suffix)
    old_revision = revision(database, cid)
    identifier = flow(database, cid, payer, payee)

    def mutate():
        database.x("update flows set amount=20.50 where case_id=? and id=?", [cid, identifier])
        database.x("update resolved_endpoints set entity_id=? where case_id=? and flow_id=? and role in ('payer','self')",
                   [successor, cid, identifier])
        added = flow(database, cid, successor, payee, when="2026-01-01 12:00:00")
        database.x("update flows set amount=1.01 where case_id=? and id=?", [cid, added])
        publish_revision(database, cid, "b" * 64)

    original_query, hits = database.q, []
    with publish_at_read_boundary(database, mutate) as boundary:
        def query(sql, params=None):
            result = original_query(sql, params)
            prefix = "select count(*) c" if read_boundary == "count" else "select *"
            if not hits and sql.lstrip().startswith(prefix + " from effective_flows as flows"):
                hits.append(True)
                boundary()
            return result

        monkeypatch.setattr(database, "q", query)
        response = client.get("/api/flows", params={"case_id": cid})
    assert hits and response.status_code == 200, response.text
    value = response.json()
    assert value["total"] == 1
    assert [(row["id"], row["amount"], row["payer_account"], row["payer_display_account"])
            for row in value["rows"]] == [(identifier, "10.25", payer, "SYN-CARD-A")]
    assert value["resolution"]["revision"] == old_revision
    assert evidence_resolution.summary(cid)["revision"] == "b" * 64
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid])[0]["n"] == 2


def test_tracked_workbook_uses_captured_resolution_and_endpoint_metadata(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic coherent workbook publication")
    payer, payee = ["fte_" + digit * 32 for digit in "ab"]
    entity(database, cid, payer, "SYN-ACCOUNT-A", "SYN-CARD-A")
    entity(database, cid, payee, "SYN-ACCOUNT-B", "SYN-CARD-B")
    old_revision = revision(database, cid)
    identifier = flow(database, cid, payer, payee)
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,
                  mode,mapping_json,sheet,parser_version,row_kind)
                  values ('synthetic-export-source',?,'synthetic.csv',?,1,'B','{}','',
                          'funds-trace-table-v19','table_record')""", [cid, "a" * 64])

    def mutate():
        database.x("update flows set amount=20.50 where case_id=? and id=?", [cid, identifier])
        for key, suffix in ((payer, "A"), (payee, "B")):
            details = json.loads(database.q_raw(
                "select details_json from resolution_entities where case_id=? and entity_id=?", [cid, key])[0]["details_json"])
            details.update(account_number="SYN-NEW-ACCOUNT-" + suffix, display_account="SYN-NEW-CARD-" + suffix)
            database.x("update resolution_entities set account_number=?,display_account=?,details_json=? where case_id=? and entity_id=?",
                       [details["account_number"], details["display_account"], json.dumps(details), cid, key])
        publish_revision(database, cid, "b" * 64)

    original_workbook, hits = main._tracked_workbook, []
    with publish_at_read_boundary(database, mutate) as boundary:
        def workbook(*args, **kwargs):
            hits.append(True)
            boundary()
            return original_workbook(*args, **kwargs)

        monkeypatch.setattr(main, "_tracked_workbook", workbook)
        response = client.get("/api/export/tracked.xlsx", params={"case_id": cid})
    assert hits and response.status_code == 200, response.text
    workbook = openpyxl.load_workbook(io.BytesIO(response.content), data_only=False)
    try:
        values = list(workbook["追踪流水"].values)
        row = dict(zip(values[0], values[1]))
        assert (row["交易金额"], row["付款账号"], row["收款账号"], row["付款显示标识"], row["付款实体ID"]) == (
            "10.25", "SYN-ACCOUNT-A", "SYN-ACCOUNT-B", "SYN-CARD-A", payer)
        pending = list(workbook["待调取账号"].values)
        row = dict(zip(pending[0], pending[1]))
        assert (row["账号"], row["显示标识"], row["实体ID"], row["交易总额"]) == (
            "SYN-ACCOUNT-B", "SYN-CARD-B", payee, "10.25")
        notes = dict(list(workbook["统计口径"].values)[1:])
        assert notes["解析修订"] == old_revision
    finally:
        workbook.close()
    assert evidence_resolution.summary(cid)["revision"] == "b" * 64


def test_tracked_export_does_not_enumerate_unrelated_case_entities(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic bounded export metadata")
    payer, payee, unrelated = ["fte_" + digit * 32 for digit in "abc"]
    for key, suffix in ((payer, "A"), (payee, "B"), (unrelated, "UNRELATED")):
        entity(database, cid, key, "SYN-ACCOUNT-" + suffix, "SYN-CARD-" + suffix)
    revision(database, cid)
    flow(database, cid, payer, payee)
    original_registry, calls = evidence_resolution.registry, []

    def registry(case_id):
        calls.append(case_id)
        return original_registry(case_id)

    monkeypatch.setattr(evidence_resolution, "registry", registry)
    response = client.get("/api/export/tracked.xlsx", params={"case_id": cid})
    assert response.status_code == 200, response.text
    assert calls == [], "a bounded tracked export must not read the whole case entity registry"
    workbook = openpyxl.load_workbook(io.BytesIO(response.content), data_only=False)
    try:
        contents = str(list(workbook["追踪流水"].values)) + str(list(workbook["待调取账号"].values))
        assert "SYN-ACCOUNT-A" in contents and "SYN-ACCOUNT-B" in contents
        assert "UNRELATED" not in contents
    finally:
        workbook.close()
