"""Public synthetic source gaps and local posting-order evidence boundaries."""
import csv
import io
import json

import pytest

from app import event_resolution
from test_event_import import csv_rows, transaction
from test_folder_evidence import accepted


def own_row(payer, payee, minute="00", order="", amount="100.00", *, reference=False):
    row = transaction("SYN-B", **{"付款账号": payer, "收款账号": payee,
        "交易流水号": "SYN-" + payer + "-" + payee,
        "交易时间": "2026-01-01 10:" + minute + ":00", "交易金额": amount,
        "本笔交易后账面余额": "", "记账顺序号": order})
    if not reference:
        row = {key: value for key, value in row.items() if key not in event_resolution.HEADERS.values()}
    return row


def request_for(client, case_id):
    response = client.get(f"/api/v1/cases/{case_id}/flows")
    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    source = next(row for row in rows if row["raw_payer_account"] == "SYN-A")
    target = next(row for row in rows if row["raw_payee_account"] == "SYN-C")
    return {"case_id": str(case_id), "account": source["self_account"], "currency": "CNY",
        "start": "2026-01-01T00:00:00", "end": "2026-01-01T23:59:59",
        "source_flow_id": source["id"], "target_flow_ids": [target["id"]],
        "conditions": {"coverage": "complete_declared", "no_overdraft": True, "funds_available": True,
                       "opening_balance": "0.00", "balance_kind": "unknown"}}


def analyze(client, case_id):
    response = client.post("/api/analysis/principal", json=request_for(client, case_id))
    assert response.status_code == 200, response.text
    return response.json()


def competing_rows():
    return [own_row("SYN-A", "SYN-B", order="1"), own_row("SYN-B", "SYN-D", order="2"),
            own_row("SYN-O", "SYN-B", order="3"), own_row("SYN-B", "SYN-C", order="4")]


def test_unparsed_competing_debit_cannot_be_overridden_by_coverage_assertion(client, make_case):
    cid = make_case("Synthetic unresolved competitor")
    rows = [own_row("SYN-A", "SYN-B"), own_row("SYN-B", "SYN-D", "01", amount="not-an-amount"),
            own_row("SYN-B", "SYN-C", "02")]
    accepted(client, cid, [("ledger.csv", csv_rows(rows))], scope_bank=None)
    stats = client.get(f"/api/v1/cases/{cid}/stats").json()
    assert stats["import_coverage"]["unresolved_records"] == 1
    result = analyze(client, cid)
    assert result["status"] == "unknown"
    assert "unresolved_source_records" in result["reasons"] and result["targets"] == []


def test_conflicting_reference_cannot_yield_exact_single_ledger_attribution(client, make_case):
    cid = make_case("Synthetic conflicting event")
    rows = [own_row("SYN-A", "SYN-B", reference=True), own_row("SYN-B", "SYN-C", "02", reference=True)]
    accepted(client, cid, [("ledger.csv", csv_rows(rows))], scope_bank=None)
    assert event_resolution.summary(cid)["conflict_entries"] == 2
    result = analyze(client, cid)
    assert result["status"] == "unknown"
    assert "conflicting_event_evidence" in result["reasons"] and result["targets"] == []


def test_same_upload_different_members_do_not_share_posting_order(client, make_case):
    cid = make_case("Synthetic unrelated posting sequences")
    accepted(client, cid, [(f"ledger-{index}.csv", csv_rows([row]))
                           for index, row in enumerate(competing_rows())], scope_bank=None)
    result = analyze(client, cid)
    assert result["status"] == "unknown" and result["reasons"] == ["posting_order_unknown"]
    assert result["targets"] == []


def test_one_member_one_region_order_survives_replay_and_package(client, make_case):
    cid = make_case("Synthetic common posting sequence")
    inputs = [("ledger.csv", csv_rows(competing_rows()))]
    accepted(client, cid, inputs, scope_bank=None)
    result = analyze(client, cid)
    assert result["status"] == "ok"
    assert (result["targets"][0]["min"], result["targets"][0]["max"], result["targets"][0]["fifo"]) == ("0.00", "0.00", "0.00")
    _, replay = accepted(client, cid, inputs, scope_bank=None, zipped=True)
    assert (replay["ok"], replay["rows_dup"]) == (0, 4)
    assert analyze(client, cid)["targets"] == result["targets"]
    package = client.get(f"/api/cases/{cid}/export")
    assert package.status_code == 200
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    restored_result = analyze(client, restored.json()["case_id"])
    assert restored_result["status"] == "ok" and restored_result["targets"][0]["max"] == "0.00"
    report = client.post("/api/analysis/principal/export", json={**request_for(client, cid), "expected_revision": analyze(client, cid)["revision"]})
    assert report.status_code == 200
    assert "order_scopes" not in report.text and "posting_scope" not in report.text


def test_repeated_header_starts_an_independent_posting_region(client, database, make_case):
    cid = make_case("Synthetic independent table regions")
    rows = competing_rows()
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows[:2])
    writer.writeheader()
    writer.writerows(rows[2:])
    accepted(client, cid, [("ledger.csv", output.getvalue().encode("utf-8-sig"))], scope_bank=None)
    result = analyze(client, cid)
    assert result["status"] == "unknown" and result["reasons"] == ["posting_order_unknown"]
    assert result["targets"] == []
    declarations = database.q_raw("select details_json from import_records where case_id=? and disposition='imported' order by source_row", [cid])
    assert [json.loads(json.loads(row["details_json"])[event_resolution.LEDGER_FIELD])["posting_scope"]
            for row in declarations] == ["1", "1", "4", "4"]
    package = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    assert analyze(client, restored.json()["case_id"])["reasons"] == ["posting_order_unknown"]


@pytest.mark.parametrize("scope", ["2", "999999", ""])
def test_package_rejects_order_scope_outside_its_header_region(client, database, make_case, scope):
    cid = make_case("Synthetic invalid posting region")
    accepted(client, cid, [("ledger.csv", csv_rows(competing_rows()))], scope_bank=None)
    record = database.q_raw("select source_id,record_no,details_json from import_records where case_id=? order by record_no limit 1", [cid])[0]
    details = json.loads(record["details_json"])
    evidence = json.loads(details[event_resolution.LEDGER_FIELD])
    evidence["posting_scope"] = scope
    details[event_resolution.LEDGER_FIELD] = json.dumps(evidence)
    database.x("update import_records set details_json=? where case_id=? and source_id=? and record_no=?",
               [json.dumps(details), cid, record["source_id"], record["record_no"]])
    package = client.get(f"/api/cases/{cid}/export")
    assert package.status_code == 200
    before = database.q_raw("select count(*) n from cases")[0]["n"]
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 400
    assert database.q_raw("select count(*) n from cases")[0]["n"] == before
