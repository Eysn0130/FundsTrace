"""Public synthetic import, event withdrawal and portable source declarations."""
import csv
import io
import json
from decimal import Decimal

import pytest

from app import event_resolution, table_import, table_archive
from test_folder_evidence import accepted, preview, complete


def transaction(own, **changes):
    row = {"交易流水号": "SYN-LOCAL-" + own, "交易时间": "2026-01-01 10:00:00",
           "付款账号": "SYN-A", "付款人开户行": "Synthetic bank", "收款账号": "SYN-B",
           "收款人开户行": "Synthetic bank", "本方账号": own, "本方开户银行": "Synthetic bank",
           "交易金额": "100.00", "本笔交易后账面余额": "50.00" if own == "SYN-A" else "200.00",
           "交易币种": "CNY", "记账顺序号": "1",
           "参考号机构": "Synthetic issuer", "参考号渠道": "Synthetic settlement",
           "参考号业务域": "Synthetic transfer 2026", "跨账簿事件参考号": "SYN-E1"}
    row.update(changes)
    return row


def csv_rows(rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def test_scoped_opposite_ledger_event_replay_conflict_withdrawal_and_package(client, database, make_case):
    cid = make_case("Synthetic event evidence")
    inputs = [("sender.csv", csv_rows([transaction("SYN-A")])), ("recipient.csv", csv_rows([transaction("SYN-B")]))]
    _, first = accepted(client, cid, inputs, scope_bank=None)
    summary = event_resolution.summary(cid)
    assert summary["confirmed_events"] == 1
    assert summary["confirmed_entries"] == 2
    assert summary["confirmed_amount_totals"] == [{"currency": "CNY", "amount": "100.00"}]
    assert summary["complete_event_total"] is False
    original = database.q_raw("select id,amount,balance,self_account from flows where case_id=? order by id", [cid])
    assert len(original) == 2 and sum(row["amount"] for row in original) == Decimal("200.00")
    assert {row["balance"] for row in original} == {Decimal("50.00"), Decimal("200.00")}
    metadata = event_resolution.ledger_metadata(cid, [str(row["id"]) for row in original])
    assert all(value["balance_kind"] == "post_book" and value["posting_order"] == "1" for value in metadata.values())
    _, replay = accepted(client, cid, list(reversed(inputs)), scope_bank=None, zipped=True)
    assert (replay["ok"], replay["rows_dup"]) == (0, 2)
    assert event_resolution.summary(cid)["confirmed_events"] == 1
    _, contradiction = accepted(client, cid, [("conflict.csv", csv_rows([transaction("SYN-A", **{"交易金额": "101.00"})]))], scope_bank=None)
    assert event_resolution.summary(cid)["confirmed_events"] == 0
    assert event_resolution.summary(cid)["conflict_entries"] == 3
    assert client.delete(f"/api/batches/{contradiction['batch_id']}", params={"case_id": cid}).status_code == 200
    assert event_resolution.summary(cid)["confirmed_events"] == 1
    package = client.get(f"/api/cases/{cid}/export")
    assert package.status_code == 200
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    new_cid = restored.json()["case_id"]
    assert event_resolution.summary(new_cid)["confirmed_amount_totals"] == summary["confirmed_amount_totals"]
    assert len(event_resolution.members(new_cid)) == 2
    assert {row["event_id"] for row in event_resolution.members(new_cid)} == {row["event_id"] for row in event_resolution.members(cid)}
    assert client.delete(f"/api/batches/{first['batch_id']}", params={"case_id": cid}).status_code == 200
    assert event_resolution.summary(cid)["confirmed_events"] == 1
    assert client.delete(f"/api/batches/{replay['batch_id']}", params={"case_id": cid}).status_code == 200
    assert event_resolution.summary(cid)["confirmed_events"] == 0
    assert event_resolution.summary(new_cid)["confirmed_events"] == 1


def test_same_second_same_amount_independent_refs_and_missing_scope_are_not_merged(client, database, make_case):
    cid = make_case("Synthetic independent transfers")
    source = [transaction("SYN-A"), transaction("SYN-A", **{"交易流水号": "SYN-LOCAL-2", "跨账簿事件参考号": "SYN-E2"})]
    _, receipt = accepted(client, cid, [("source.csv", csv_rows(source))], scope_bank=None)
    assert receipt["ok"] == 2 and event_resolution.summary(cid)["confirmed_events"] == 0
    assert event_resolution.summary(cid)["unknown_entries"] == 2
    accepted(client, cid, [("recipient.csv", csv_rows([transaction("SYN-B"), transaction("SYN-B", **{"交易流水号": "SYN-OTHER-2", "跨账簿事件参考号": "SYN-E2"})]))], scope_bank=None)
    assert event_resolution.summary(cid)["confirmed_events"] == 2
    assert event_resolution.summary(cid)["confirmed_amount_totals"] == [{"currency": "CNY", "amount": "200.00"}]
    another = make_case("Synthetic ordinary references")
    ordinary = [{key: value for key, value in transaction(own).items() if key not in event_resolution.HEADERS.values()} for own in ("SYN-A", "SYN-B")]
    accepted(client, another, [("ordinary.csv", csv_rows(ordinary))], scope_bank=None)
    assert event_resolution.summary(another) is None
    assert database.q_raw("select count(*) n from effective_flows where case_id=?", [another])[0]["n"] == 2


def test_reference_scope_does_not_supply_missing_account_institution(client, database, make_case):
    cid = make_case("Synthetic unknown ledger institutions")
    rows = [transaction(own, **{"付款人开户行": "", "收款人开户行": "", "本方开户银行": ""}) for own in ("SYN-A", "SYN-B")]
    accepted(client, cid, [("both.csv", csv_rows(rows))], scope_bank=None)
    assert event_resolution.summary(cid)["confirmed_events"] == 0
    assert event_resolution.summary(cid)["unknown_entries"] == 2
    assert {row["reason"] for row in event_resolution.members(cid)} == {"incomplete_ledger_identity"}


def test_old_parser_does_not_reinterpret_new_columns_and_new_parser_retains_declarations():
    payload = csv_rows([transaction("SYN-A")])
    old = table_import.parse(io.BytesIO(payload), suffix=".csv", profile=table_import.LEGACY_EVIDENCE_PROFILE)
    current = table_import.parse(io.BytesIO(payload), suffix=".csv", profile=table_import.EVIDENCE_PROFILE)
    assert old["rows"][0]["amount"] == current["rows"][0]["amount"] == Decimal("100.00")
    assert event_resolution.DETAIL_FIELD not in old["records"][0]["details"]
    assert event_resolution.validate_reference(current["records"][0]["details"][event_resolution.DETAIL_FIELD])["reference"] == "SYN-E1"
    assert table_import.LEGACY_EVIDENCE_PROFILE in table_import.SUPPORTED_PROFILES
    assert table_archive.LEGACY_EVIDENCE_PROFILE in table_archive.SUPPORTED_PROFILES


def test_legacy_evidence_reimport_adds_declaration_without_rewriting_ledger(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic parser-version upgrade")
    inputs = [("ledgers.csv", csv_rows([transaction("SYN-A"), transaction("SYN-B")]))]
    with monkeypatch.context() as previous:
        previous.setattr(table_archive, "EVIDENCE_PROFILE", table_archive.LEGACY_EVIDENCE_PROFILE)
        accepted(client, cid, inputs, scope_bank=None)
    original = database.q_raw("select * from flows where case_id=? order by id", [cid])
    assert event_resolution.summary(cid) is None
    _, result = accepted(client, cid, inputs, scope_bank=None)
    assert (result["ok"], result["rows_dup"]) == (0, 2)
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == original
    assert event_resolution.summary(cid)["confirmed_events"] == 1
    exported = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", exported.content)})
    assert restored.status_code == 200, restored.text
    assert event_resolution.summary(restored.json()["case_id"])["confirmed_events"] == 1


def test_unresolved_claim_revokes_and_removing_its_empty_batch_restores_event(client, database, make_case):
    cid = make_case("Synthetic unresolved event contradiction")
    accepted(client, cid, [("both.csv", csv_rows([transaction("SYN-A"), transaction("SYN-B")]))], scope_bank=None)
    _, unresolved = accepted(client, cid, [("unresolved.csv", csv_rows([transaction("SYN-A", **{"交易金额": "not-an-amount"})]))], scope_bank=None)
    summary = event_resolution.summary(cid)
    assert summary["confirmed_events"] == 0 and summary["conflict_entries"] == 2
    assert summary["unresolved_reference_observations"] == 1
    assert client.delete(f"/api/batches/{unresolved['batch_id']}", params={"case_id": cid}).status_code == 200
    assert event_resolution.summary(cid)["confirmed_events"] == 1


def test_event_projection_failure_rolls_back_import_and_revision(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic event rollback")
    accepted(client, cid, [("both.csv", csv_rows([transaction("SYN-A"), transaction("SYN-B")]))], scope_bank=None)
    tables = ("flows", "flow_sources", "import_sources", "import_records", "evidence_sources", "event_state", "event_members", "resolved_events", "analysis_state")
    before = {table: database.q_raw(f"select * from {table} where case_id=? order by all", [cid]) for table in tables}
    view = preview(client, cid, [("contradiction.csv", csv_rows([transaction("SYN-A", **{"交易金额": "101.00"})]))], scope_bank=None)
    writer = database.insert_dataframe

    def fail_after_write(table, frame, columns):
        result = writer(table, frame, columns)
        if table == "event_members":
            raise RuntimeError("Synthetic event write failure")
        return result

    with monkeypatch.context() as failing:
        failing.setattr(database, "insert_dataframe", fail_after_write)
        job = complete(client, cid, view)
    assert job["stage"] == "error"
    assert {table: database.q_raw(f"select * from {table} where case_id=? order by all", [cid]) for table in tables} == before


@pytest.mark.parametrize("mutation", ["unknown_policy", "extra_field", "missing_scope", "old_profile"])
def test_package_rejects_unversioned_or_invalid_event_authority(client, database, make_case, mutation):
    cid = make_case("Synthetic event admission")
    accepted(client, cid, [("sender.csv", csv_rows([transaction("SYN-A")]))], scope_bank=None)
    record = database.q_raw("select source_id,record_no,details_json from import_records where case_id=?", [cid])[0]
    details = json.loads(record["details_json"])
    value = json.loads(details[event_resolution.DETAIL_FIELD])
    if mutation == "unknown_policy":
        value["policy"] = "unverified-future-policy"
    elif mutation == "extra_field":
        value["trusted"] = True
    elif mutation == "missing_scope":
        value["business_scope"] = ""
    else:
        database.x("update import_sources set parser_version=? where case_id=?", [table_archive.LEGACY_EVIDENCE_PROFILE, cid])
    details[event_resolution.DETAIL_FIELD] = json.dumps(value)
    database.x("update import_records set details_json=? where case_id=? and source_id=? and record_no=?",
               [json.dumps(details), cid, record["source_id"], record["record_no"]])
    package = client.get(f"/api/cases/{cid}/export")
    assert package.status_code == 200
    before = database.q_raw("select count(*) n from cases")[0]["n"]
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 400
    assert database.q_raw("select count(*) n from cases")[0]["n"] == before
