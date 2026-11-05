"""Independent native31 booked movements, never settlement assertions."""
import io
import json

from openpyxl import load_workbook
import pytest

from test_bank_posting_pairs import encoded, targets, tracked_graph
from test_source_contract_68 import HEADERS
from test_source_ledger import add_source


def entries():
    rows = []
    for index, (amount, balance) in enumerate((("0.00", "1000.00"), ("12.34", "987.66"), ("12.34", "975.32"))):
        row = dict.fromkeys(HEADERS, "")
        row.update({"交易账号": "SYN-SINGLE-A", "交易卡号": "SYN-SINGLE-CARD", "交易对手账卡号": "SYN-SINGLE-B",
            "对手开户银行": "Synthetic counter bank", "收付标志": "出", "交易币种": "CNY",
            "交易时间": f"2039-04-05 09:00:0{index}", "交易金额": amount, "交易余额": balance,
            "交易流水号": "SYN-PREV" if index == 0 else f"SYN-SINGLE-{index}",
            "凭证号": f"SYN-VOUCHER-{index}", "日志号": f"SYN-LOG-{index}", "传票号": f"SYN-TICKET-{index}"})
        rows.append(row)
    return rows


def assert_exact(client, cid):
    page = client.get(f"/api/v1/cases/{cid}/flows").json()
    selected = [row for row in page["items"] if not row["trade_no"].startswith("SYN-PREV")]
    assert len(selected) == 2
    assert {row["balance"] for row in selected} == {"987.66", "975.32"}
    graph = tracked_graph(client, cid, targets(client, cid))
    assert (graph["transaction_count"], graph["edges"][0]["total"]) == (2, "24.68")
    assert graph["funds"]["status"] == "exact"
    assert graph["funds"]["count"] == {"lower": 2, "upper": 2}
    assert graph["funds"]["totals"] == [{"currency": "CNY", "amount": "24.68", "lower": "24.68", "upper": "24.68"}]
    return selected


def test_distinct_independently_balanced_postings_are_exact_and_keep_all_source_references(client, database, make_case):
    cid = make_case("SYN independently balanced singles")
    raw = encoded(entries())
    view, _ = add_source(client, cid, raw)
    selected = assert_exact(client, cid)
    proofs = database.q_raw("select evidence_json from resolved_events where case_id=?", [cid])
    assert len(proofs) == 2
    for proof in proofs:
        proof = json.loads(proof["evidence_json"])
        assert proof["policy"] == "independent-native31-single-posting-v3"
        assert proof["balance_change"]["difference"] == "-12.34"
        assert proof["meaning"] == "local_booked_movement_only; external_settlement_unproved"
    for row in selected:
        source, = client.get(f"/api/v1/cases/{cid}/flows/{row['id']}/sources").json()["items"]
        assert "current_bank_single_posting_proof" in source["record_details"]
        assert "current_bank_posting_pair_proof" not in source["record_details"]
        fields = json.loads(source["record_details"]["source_field_evidence"])
        assert all(any(item["column"] == name and item["raw"].startswith("SYN-") for item in fields)
                   for name in ("凭证号", "日志号", "传票号"))
    exported = client.get("/api/export/tracked.xlsx", params={"case_id": cid})
    assert exported.status_code == 200
    book = load_workbook(io.BytesIO(exported.content), data_only=True)
    rows = list(book["资金统计"].values)
    result, = [dict(zip(rows[0], row)) for row in rows[1:]]
    assert result["精确事件金额"] == result["已确认下界"] == result["可能上界"] == "24.68"
    assert result["事件数下界"] == result["事件数上界"] == 2
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == raw
    report = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()["items"]
    assert sum("current_bank_single_posting_proof" in row["details"] for row in report) == 2


def test_duplicate_support_late_primary_conflict_and_withdrawal_recompute_singles(client, make_case):
    cid = make_case("SYN reversible single proof")
    raw = encoded(entries());view, _ = add_source(client, cid, raw)
    receipt_url = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    receipt = client.get(receipt_url).json()
    assert_exact(client, cid)
    _, duplicate = add_source(client, cid, raw)
    assert_exact(client, cid)
    late = dict(entries()[1], **{"交易时间": "2039-04-05 09:00:03", "交易余额": "962.98"})
    _, conflict = add_source(client, cid, encoded([late]), "late.csv")
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert graph["funds"]["confirmed_event_count"] == 1
    assert graph["funds"]["totals"][0]["amount"] is None
    assert client.delete(f"/api/batches/{conflict['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert_exact(client, cid)
    assert client.delete(f"/api/batches/{duplicate['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert_exact(client, cid)
    assert client.get(receipt_url).json() == receipt
    package = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("singles.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    assert_exact(client, restored.json()["case_id"])


@pytest.mark.parametrize("change", ["missing_clock", "missing_amount", "same_target_time", "wrong_balance", "same_primary", "missing_bank"])
def test_incomplete_book_or_target_proof_stays_unresolved(client, database, make_case, change):
    rows = entries()
    if change == "missing_clock": rows[0]["交易时间"] = ""
    elif change == "missing_amount": rows[0]["交易金额"] = ""
    elif change == "same_target_time": rows[1]["交易时间"] = rows[2]["交易时间"]
    elif change == "wrong_balance": rows[1]["交易余额"] = "987.65"
    elif change == "same_primary": rows[1]["交易流水号"] = rows[2]["交易流水号"]
    else:
        for row in rows: row["对手开户银行"] = ""
    cid = make_case("SYN missing independent proof")
    add_source(client, cid, encoded(rows))
    assert not database.q_raw("select * from resolved_events where case_id=?", [cid])


@pytest.mark.parametrize("prior_kind", ["negative", "unknown_direction"])
def test_negative_and_unoriented_rows_remain_available_as_previous_balance_observations(client, make_case, prior_kind):
    rows = entries()
    if prior_kind == "negative": rows[0]["交易金额"] = "-1.00"
    else:
        rows[0]["收付标志"] = ""
        rows[0]["交易对手账卡号"] = rows[0]["对手开户银行"] = ""
        rows[0]["交易是否成功"] = "1"
    cid = make_case("SYN retained source history")
    add_source(client, cid, encoded(rows))
    assert_exact(client, cid)


def test_empty_direction_and_empty_status_remains_unadmitted_and_traceable(client, database, make_case):
    rows = entries();rows[0]["收付标志"] = "";rows[0]["交易对手账卡号"] = rows[0]["对手开户银行"] = ""
    raw = encoded(rows);cid = make_case("SYN unknown status source boundary")
    view, _ = add_source(client, cid, raw)
    records = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()["items"]
    first = next(row for row in records if row["source_row"] == 2)
    assert first["reason_code"] == "unknown_status" and first["flow_id"] is None
    assert "source_ledger_evidence" not in first["details"]
    assert not database.q_raw("select * from resolved_events where case_id=?", [cid])
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == raw


@pytest.mark.parametrize("clock", ["2039-04-05 09:00:01", "2039-04-05 09:00:02", "2041-01-01 00:00:00", "", "2039-04-05T09:00:01+08:00"])
def test_late_different_primary_counterbook_candidate_revokes_without_merging(client, database, make_case, clock):
    cid = make_case("SYN cross-book uncertainty")
    add_source(client, cid, encoded(entries()))
    assert_exact(client, cid)
    before = dict(entries()[0], **{"交易账号": "SYN-SINGLE-B", "交易卡号": "SYN-OTHER-CARD",
        "交易对手账卡号": "SYN-SINGLE-A", "收付标志": "进", "交易余额": "500.00", "交易流水号": "SYN-PREV-OTHER"})
    opposite = dict(before, **{"交易流水号": "SYN-DIFFERENT-PRIMARY", "交易时间": clock,
        "交易金额": "12.34", "交易余额": "512.34"})
    _, late = add_source(client, cid, encoded([before, opposite]), "opposite.csv")
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert graph["transaction_count"] == 2
    assert graph["edges"][0]["total"] == '24.68'
    assert graph["funds"]["totals"][0]["amount"] is None
    assert not database.q_raw("select * from resolved_events where case_id=? and amount=12.34 and evidence_json like '%SYN-SINGLE-1%'", [cid])
    assert all(len(json.loads(row["evidence_json"])["source_rows"]) == 1 for row in
               database.q_raw("select evidence_json from resolved_events where case_id=?", [cid]))
    assert client.delete(f"/api/batches/{late['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert_exact(client, cid)


def test_primary_reuse_across_counterparty_banks_cannot_confirm_singletons(client, database, make_case):
    cid = make_case("SYN global primary competition")
    rows = entries();rows[2]["交易流水号"] = rows[1]["交易流水号"];rows[2]["对手开户银行"] = "Synthetic different counter bank"
    add_source(client, cid, encoded(rows))
    assert not database.q_raw("select * from resolved_events where case_id=?", [cid])


def test_named_primary_with_tab_crlf_in_nonfinancial_other_family_revokes_singleton(client, make_case):
    from app.feedback_import import COLUMNS
    cid = make_case("SYN literal primary whitespace")
    add_source(client, cid, encoded(entries()))
    assert_exact(client, cid)
    row = dict(entries()[1], **{"交易账号": "SYN-UNRELATED", "交易卡号": "SYN-UNRELATED-CARD",
        "交易流水号": "SYN-SINGLE-1\t\r\n", "交易金额": "", "对手开户银行": "Synthetic unrelated bank"})
    _, late = add_source(client, cid, encoded([row], headers=COLUMNS), "other-family.csv")
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert graph["funds"]["confirmed_event_count"] == 1
    assert client.delete(f"/api/batches/{late['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert_exact(client, cid)


def test_earlier_projection_rebuild_keeps_source_journal_and_receipt_unchanged(client, database, make_case):
    from app import event_resolution
    cid = make_case("SYN v4 derived projection compatibility")
    view, _ = add_source(client, cid, encoded(entries()))
    before = database.q_raw("select * from import_records where case_id=? order by record_no", [cid])
    receipt_url = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    receipt = client.get(receipt_url).json()
    summary = event_resolution.summary(cid);summary["policy"] = "scoped-source-postings-v4"
    with database.transaction():
        database.x("delete from resolved_events where case_id=?", [cid])
        database.x("update event_members set status='unknown',event_id=null where case_id=?", [cid])
        database.x("update event_state set summary_json=? where case_id=?", [json.dumps(summary), cid])
    with database._lock:
        database.con().execute("checkpoint")
        database._con.close();database._con = None
        database.con()
    assert_exact(client, cid)
    assert database.q_raw("select * from import_records where case_id=? order by record_no", [cid]) == before
    assert client.get(receipt_url).json() == receipt
