"""Synthetic source postings stay usable without inventing economic effects."""
import csv
import gc
from decimal import Decimal
import io
import json
import zipfile
import weakref

import pytest

from test_folder_evidence import complete
from test_source_contract_68 import HEADERS


@pytest.mark.parametrize("zero_witness", [False, True])
def test_current_cross_container_cancellation_keeps_signed_observations_and_withdrawal(client, make_case, zero_witness):
    from test_table_cancellations import round_trip, source
    rows = round_trip()
    if zero_witness:
        rows[0]["交易金额"] = "0.00"
    original, package = source(rows, path="table.csv")
    cid = make_case("SYN current source ledger cancellation support")
    ordinary_view, ordinary = add_source(client, cid, original, "source.csv")
    receipt_url = f"/api/v1/cases/{cid}/imports/{ordinary_view['import_id']}"
    old_receipt = client.get(receipt_url).json()
    before = "10.00" if zero_witness else "11.00"
    after = "0.00" if zero_witness else "1.00"
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["amount_total"] == before
    proofs = []
    for _ in range(2):
        view, job = add_source(client, cid, package, "proof.zip")
        assert view["table_summary"]["profile"] == "funds-trace-table-archive-v57"
        assert job["result"]["table_summary"]["cancelled"] == 2
        proofs.append(job)
        page = client.get(f"/api/v1/cases/{cid}/flows").json()
        assert page["total"] == 3
        assert sorted(item["amount"] for item in page["items"]) == ["-10.00", "0.00" if zero_witness else "1.00", "10.00"]
        assert client.get(f"/api/v1/cases/{cid}/stats").json()["amount_total"] == after
        assert client.get(receipt_url).json() == old_receipt
    packed = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("ledger.fundcase", packed.content)})
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/v1/cases/{restored.json()['case_id']}/stats").json()["amount_total"] == after
    for index, job in enumerate(proofs):
        assert client.delete(f"/api/batches/{job['result']['batch_id']}", params={"case_id": cid}).status_code == 200
        assert client.get(f"/api/v1/cases/{cid}/stats").json()["amount_total"] == (after if index == 0 else before)
    assert client.get(receipt_url).json() == old_receipt


def test_current_negative_ledger_can_witness_a_proved_chain_without_hiding_changed_facts(client, database, make_case):
    from test_table_cancellation_container import chain_rows
    from test_table_cancellations import source
    cid = make_case("SYN current source ledger cancellation chain")
    original, package = source(chain_rows(), path="table.csv")
    add_source(client, cid, original, "source.csv")
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["amount_total"] == "42.00"
    _, proof = add_source(client, cid, package, "proof.zip")
    assert proof["result"]["table_summary"]["cancelled"] == 6
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["amount_total"] == "2.00"
    _, repeated = add_source(client, cid, package, "proof.zip")
    assert repeated["result"]["table_summary"]["cancelled"] == 6
    page = client.get(f"/api/v1/cases/{cid}/flows").json()
    assert page["total"] == 8 and page["funds"]["observation_count"] == 2
    changed = next(item for item in page["items"] if item["amount"] == "-20.00")
    database.x("update flows set amount=-19.99 where case_id=? and id=?", [cid, int(changed["id"])])
    _, rejected_proof = add_source(client, cid, package, "proof.zip")
    assert rejected_proof["result"]["table_summary"]["cancelled"] == 4
    # The independent pair and the unmodified first pair survive; the changed
    # stored negative cannot support the second pair or be silently overwritten.
    assert database.q_raw("select amount from flows where case_id=? and id=?", [cid, int(changed["id"])])[0]["amount"] == Decimal('-19.99')


def payload(*, archive=False, reverse=False, zero_status="01"):
    rows = []
    for number, stamp, value, balance, counter, label, status in [
        ("SYN-WITNESS", "2037-02-03 09:00:00", "10.00", "100.00", "000B", "转账", "成功"),
        ("SYN-NEGATIVE", "2037-02-03 09:00:01", "-5.00", "105.00", "000B", "退费", "成功"),
        ("", "2037-02-03 09:00:02", "0.00", "105.00", "", "柜台", zero_status),
    ]:
        values = {"交易账号": "000A", "交易方户名": "SYN owner", "交易时间": stamp,
                  "交易金额": value, "交易余额": balance, "收付标志": "出", "交易对手账卡号": counter,
                  "交易流水号": number, "凭证号": "SYN independent voucher" if number else "",
                  "交易币种": "CNY", "交易是否成功": status, "交易类型": label,
                  "摘要说明": "SYN original " + label, "备注": "SYN independent remark"}
        rows.append([values.get(header, "") for header in HEADERS])
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, quoting=csv.QUOTE_ALL)
    writer.writerow(HEADERS)
    writer.writerows(rows[::-1] if reverse else rows)
    data = stream.getvalue().encode("utf-8-sig")
    if archive:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as package:
            package.writestr("source.csv", data)
        return output.getvalue()
    return data


def imported(client, make_case, *, archive=False, reverse=False, zero_status="01"):
    cid = make_case("SYN source postings")
    original = payload(archive=archive, reverse=reverse, zero_status=zero_status)
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": (
        "source.zip" if archive else "source.csv", original)})
    assert response.status_code == 200, response.text
    view = response.json()
    result = complete(client, cid, view)
    assert result["stage"] == "done", result
    return cid, view, original


@pytest.mark.parametrize("archive", [False, True], ids=["csv", "zip"])
def test_nonpositive_source_rows_are_first_class_without_reversing_or_abs_counting(client, make_case, archive):
    cid, view, original = imported(client, make_case, archive=archive)
    response = client.get(f"/api/v1/cases/{cid}/flows")
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 3
    values = {row["trade_no"]: row for row in page["items"]}
    negative, zero = values["SYN-NEGATIVE"], values[""]
    assert (negative["amount"], negative["payer_account"], negative["payee_account"]) == ("-5.00", "000A", "000B")
    assert negative["source_ledger"]["source_direction"] == "出"
    assert negative["source_ledger"]["economic_effect"] == "unknown"
    assert negative["source_ledger"]["economic_amount"] is None
    assert negative["source_ledger"]["balance_change"]["difference"] == "5.00"
    assert negative["source_ledger"]["balance_change"]["status"] == "observed"
    assert (zero["amount"], zero["payer_account"], zero["payee_account"], zero["trade_no"]) == ("0.00", "000A", "", "")
    assert zero["source_ledger"]["economic_effect"] == "no_movement"
    assert zero["source_ledger"]["economic_amount"] == "0.00"
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["amount_total"] == "10.00"
    for row in (negative, zero):
        sources = client.get(f"/api/v1/cases/{cid}/flows/{row['id']}/sources")
        assert sources.status_code == 200 and sources.json()["total"] == 1
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == original


def changed_payload(changes=(), *, selected=None, reverse=False):
    cells = list(csv.reader(io.StringIO(payload().decode("utf-8-sig"))))
    for index, values in changes:
        for header, value in values.items():
            cells[index + 1][HEADERS.index(header)] = value
    rows = cells[1:]
    if selected is not None:
        rows = [rows[index] for index in selected]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(cells[0])
    writer.writerows(rows[::-1] if reverse else rows)
    return stream.getvalue().encode("utf-8-sig")


def add_source(client, cid, data, name="source.csv"):
    from test_automatic_table_import import preview
    view = preview(client, cid, data, name)
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    return view, job


def test_empty_canvas_does_not_load_case_source_snapshots(client, make_case, monkeypatch):
    from app import source_ledger
    cid = make_case("SYN empty canvas source read")
    add_source(client, cid, payload())
    validated = []
    validate = source_ledger.validate
    def observe(value):
        result = validate(value)
        validated.append(result["row"]["self_account"])
        return result
    monkeypatch.setattr(source_ledger, "validate", observe)
    response = client.get("/api/graph", params={"case_id": cid})
    assert response.status_code == 200, response.text
    assert response.json()["nodes"] == response.json()["edges"] == []
    assert validated == []


def test_account_source_read_keeps_its_balance_context_without_unrelated_members(client, make_case, monkeypatch):
    from app import source_ledger
    cid = make_case("SYN selected source context")
    add_source(client, cid, payload())
    other = changed_payload([(0, {"交易账号": "SYN-UNRELATED", "交易流水号": "SYN-OTHER"})], selected=[0])
    add_source(client, cid, other, "other.csv")
    validated = []
    validate = source_ledger.validate
    def observe(value):
        result = validate(value)
        validated.append(result["row"]["self_account"])
        return result
    monkeypatch.setattr(source_ledger, "validate", observe)
    response = client.get(f"/api/v1/cases/{cid}/flows", params={"account": "000A"})
    assert response.status_code == 200, response.text
    negative, = [row for row in response.json()["items"] if row["amount"] == "-5.00"]
    assert negative["source_ledger"]["balance_change"]["difference"] == "5.00"
    assert negative["source_ledger"]["balance_change"]["basis"] == "unique_preceding_source_time"
    assert validated and "SYN-UNRELATED" not in validated


def test_source_read_does_not_retain_decoded_case_snapshots_after_response(client, make_case, monkeypatch):
    from app import source_ledger
    cid = make_case("SYN source snapshot lifetime")
    add_source(client, cid, payload())
    class Snapshot(dict):
        pass
    references = []
    validate = source_ledger.validate
    def observe(value):
        snapshot = Snapshot(validate(value))
        references.append(weakref.ref(snapshot))
        return snapshot
    monkeypatch.setattr(source_ledger, "validate", observe)
    response = client.get(f"/api/v1/cases/{cid}/flows", params={"account": "000A"})
    assert response.status_code == 200, response.text
    gc.collect()
    assert references and all(reference() is None for reference in references)


@pytest.mark.parametrize("archive", [False, True], ids=["csv", "zip"])
def test_query_account_graph_context_export_and_package_keep_ledger_scope(client, make_case, archive):
    from openpyxl import load_workbook
    cid, _, _ = imported(client, make_case, archive=archive)
    page = client.get("/api/flows", params={"case_id": cid, "account": "000A"}).json()
    assert page["total"] == 3
    assert page["funds"]["observation_count"] == 1
    stats = client.get(f"/api/v1/cases/{cid}/stats").json()
    assert stats["source_ledger"] == {"count": 2, "unknown_effect_count": 1, "zero_count": 1, "paired_count": 0}
    for endpoint in (f"/api/accounts/000A/profile?case_id={cid}", f"/api/account-registry/profile?case_id={cid}&account=000A"):
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        profile = response.json()
        assert profile["out_amt"] == "10.00" and profile["source_ledger"]["count"] == 2
        assert len(profile["source_ledger"]["records"]) == 2
    ids = [row["id"] for row in page["rows"]]
    tracked = client.post("/api/track", json={"case_id": cid, "flow_ids": ids})
    assert tracked.status_code == 200, tracked.text
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert {row["account"] for row in graph["nodes"]} == {"000A", "000B"}
    edge, = graph["edges"]
    assert (edge["source"], edge["target"], edge["cnt"], edge["total"]) == ("000A", "000B", 1, "10.00")
    assert edge["source_ledger"]["count"] == 1 and "-5.00" in edge["source_ledger_label"]
    assert graph["source_ledger"]["count"] == 2
    zero = next(row for row in page["rows"] if row["amount"] == "0.00")
    context = client.get(f"/api/context/{zero['id']}", params={"case_id": cid}).json()
    assert context["account"] == "000A" and context["side"] == "payer"
    assert len(context["rows"]) == 3 and context["anchor"]["source_ledger"]["economic_amount"] == "0.00"
    assert context["principal"]["status"] == "unknown"
    exported = client.get("/api/export/tracked.xlsx", params={"case_id": cid})
    assert exported.status_code == 200, exported.text
    workbook = load_workbook(io.BytesIO(exported.content))
    rows = list(workbook["追踪流水"].values)
    records = [dict(zip(rows[0], row)) for row in rows[1:]]
    assert {row["交易金额"] for row in records} == {"10.00", "-5.00", "0.00"}
    negative = next(row for row in records if row["交易金额"] == "-5.00")
    assert (negative["来源标注方向"], negative["资金影响"], negative["本方客观余额差"]) == ("出", "未确定", "5.00")
    package = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("source.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    new_id = restored.json()["case_id"]
    restored_page = client.get(f"/api/v1/cases/{new_id}/flows").json()
    assert restored_page["total"] == 3 and restored_page["funds"]["observation_count"] == 1
    assert client.get("/api/graph", params={"case_id": new_id}).json()["source_ledger"]["count"] == 2


@pytest.mark.parametrize("selected", [[1], [2]])
def test_ledger_only_accounts_are_discoverable_and_track_without_fabricated_endpoints(client, make_case, selected):
    cid = make_case("SYN only source ledger")
    add_source(client, cid, changed_payload(selected=selected))
    page = client.get(f"/api/v1/cases/{cid}/flows").json()
    assert page["total"] == 1 and page["funds"]["observation_count"] == 0
    assert client.get("/api/account-suggestions", params={"case_id": cid, "q": "000A"}).json()["items"][0]["account"] == "000A"
    assert client.get("/api/account-registry/profile", params={"case_id": cid, "account": "000A"}).status_code == 200
    result = client.post("/api/track", json={"case_id": cid, "flow_ids": [page["items"][0]["id"]]})
    assert result.status_code == 200, result.text
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert graph["transaction_count"] == 0 and graph["source_ledger"]["count"] == 1
    if selected == [1]:
        edge, = graph["edges"]
        assert (edge["source"], edge["target"], edge["cnt"], edge["total"], edge["kind"]) == ("000A", "000B", 0, None, "source_ledger")
    else:
        assert [row["account"] for row in graph["nodes"]] == ["000A"] and not graph["edges"]


@pytest.mark.parametrize("reverse", [False, True])
def test_source_clock_instead_of_file_order_and_offset_survives_early_return(client, make_case, reverse):
    cid = make_case("SYN signed offsets")
    data = changed_payload([(n, {"交易时间": f"2037-02-03T09:00:0{n}+08:00"}) for n in range(3)], reverse=reverse)
    add_source(client, cid, data)
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    negative = next(row for row in rows if row["amount"] == "-5.00")
    zero = next(row for row in rows if row["amount"] == "0.00")
    assert negative["trade_time_offset"] == zero["trade_time_offset"] == "+08:00"
    assert negative["trade_time_utc"].startswith("2037-02-03T01:00:01")
    assert negative["source_ledger"]["balance_change"]["difference"] == "5.00"
    assert zero["source_ledger"]["balance_change"]["difference"] == "0.00"


@pytest.mark.parametrize("changes,reason", [
    ([(0, {"交易时间": "2037-02-03 09:00:01"})], "posting_order_unknown"),
    ([(0, {"交易时间": "2037-02-03T09:00:00+08:00"})], "time_basis_incomparable"),
    ([(0, {"交易余额": ""})], "preceding_balance_missing"),
])
def test_balance_ambiguity_does_not_invent_previous_posting(client, make_case, changes, reason):
    cid = make_case("SYN ambiguous source balance")
    add_source(client, cid, changed_payload(changes))
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    negative = next(row for row in rows if row["amount"] == "-5.00")
    change = negative["source_ledger"]["balance_change"]
    assert (change["status"], change["difference"], change["reason"]) == ("unknown", None, reason)


def test_reimport_support_withdrawal_case_isolation_and_old_receipt_are_distinct(client, database, make_case, monkeypatch):
    from app import table_import
    from app.services import imports
    cid = make_case("SYN old ledger upgrade")
    with monkeypatch.context() as old:
        old.setattr(imports, "TABLE_PROFILE", table_import.BANK31_VARIANT_PROFILE)
        old_view, _ = add_source(client, cid, payload())
    old_receipt = client.get(f"/api/v1/cases/{cid}/imports/{old_view['import_id']}").json()
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 1
    with database._lock:
        database._con.close()
        database._con = None
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 1
    new_view, first = add_source(client, cid, payload())
    before = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    _, second = add_source(client, cid, payload())
    after = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert {row["id"] for row in before} == {row["id"] for row in after} and len(after) == 3
    assert client.get(f"/api/v1/cases/{cid}/imports/{old_view['import_id']}").json() == old_receipt
    for job, remaining in ((first, 3), (second, 1)):
        response = client.delete(f"/api/batches/{job['result']['batch_id']}", params={"case_id": cid})
        assert response.status_code == 200, response.text
        assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == remaining
    foreign = make_case("SYN other ledger case")
    assert client.get(f"/api/v1/cases/{foreign}/flows/{after[0]['id']}/sources").status_code == 404
    assert client.get(f"/api/v1/cases/{cid}/imports/{new_view['import_id']}/records").json()["total"] == 3


@pytest.mark.parametrize("refund_only", [False, True])
def test_strict_cancellation_and_label_only_remain_different_in_all_readers(client, make_case, refund_only):
    from test_table_cancellations import round_trip, source
    rows = round_trip()
    if refund_only:
        rows[2]["摘要说明"] = "退款"
    cid = make_case("SYN proven cancellation" if not refund_only else "SYN refund label")
    add_source(client, cid, source(rows)[1], "source.zip")
    page = client.get(f"/api/v1/cases/{cid}/flows").json()
    assert page["total"] == 3
    stats = client.get(f"/api/v1/cases/{cid}/stats").json()
    assert stats["amount_total"] == ("11.00" if refund_only else "1.00")
    ledger = [row for row in page["items"] if row["source_ledger"]]
    assert len(ledger) == (1 if refund_only else 2)
    assert {row["source_ledger"]["economic_effect"] for row in ledger} == ({"unknown"} if refund_only else {"paired_cancellation"})
    package = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("source.fundcase", package.content)})
    assert restored.status_code == 200, restored.text


@pytest.mark.parametrize("field,value", [
    ("kind", "financial"), ("signed_amount", "5.00"), ("source_currency", "USD"),
    ("direction", "in"), ("header_scope", 999),
])
def test_package_cannot_promote_or_relabel_a_signed_source_observation(client, make_case, tmp_path, field, value):
    from test_feedback_package import _members, _mutate, _archive
    cid, _, _ = imported(client, make_case)
    package = client.get(f"/api/cases/{cid}/export")
    members = _members(package.content)
    records = client.get(f"/api/v1/cases/{cid}/imports").json()["items"]
    record = client.get(f"/api/v1/cases/{cid}/imports/{records[0]['import_id']}/records").json()["items"][1]
    details = dict(record["details"])
    evidence = json.loads(details["source_ledger_evidence"])
    evidence[field] = value
    details["source_ledger_evidence"] = json.dumps(evidence)
    encoded = json.dumps(details, ensure_ascii=False).replace("'", "''")
    _mutate(members, "import_records", f"update entries set details_json='{encoded}' where record_no=2", tmp_path)
    response = client.post("/api/cases/import", files={"file": ("tampered.fundcase", _archive(members))})
    assert response.status_code == 400, response.text


@pytest.mark.parametrize("changes", [
    {"交易金额": "-5.001"}, {"收付标志": "未知"}, {"交易币种": "USD/CNY"},
])
def test_real_semantic_conflicts_do_not_gain_normal_ledger_admission(client, make_case, changes):
    cid = make_case("SYN conflicting source ledger")
    view, _ = add_source(client, cid, changed_payload([(1, changes)], selected=[1]))
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    assert records[0]["disposition"] == "unresolved"


def test_unknown_currency_is_not_defaulted_and_duplicate_voucher_does_not_pair(client, make_case):
    cid = make_case("SYN source currency and voucher")
    add_source(client, cid, changed_payload([(1, {"交易币种": "", "交易流水号": "SYN-WITNESS", "摘要说明": "冲正"})]))
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    negative = next(row for row in rows if row["amount"] == "-5.00")
    assert negative["currency"] == "" and negative["source_ledger"]["economic_effect"] == "unknown"
    assert negative["source_ledger"]["balance_change"]["reason"] == "currency_unknown"


def test_withdrawn_nonpositive_journal_stays_portable_and_does_not_restore_money(client, make_case):
    cid = make_case("SYN withdrawn source history")
    view, job = add_source(client, cid, payload())
    initial = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    response = client.delete(f"/api/batches/{job['result']['batch_id']}", params={"case_id": cid})
    assert response.status_code == 200, response.text
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    for retained, original in zip(records[1:], initial[1:]):
        assert retained["flow_id"] is None and original["flow_id"] is not None
        assert {k: v for k, v in retained.items() if k != "flow_id"} == {k: v for k, v in original.items() if k != "flow_id"}
    package = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("history.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    restored_id = restored.json()["case_id"]
    assert client.get(f"/api/v1/cases/{restored_id}/flows").json()["total"] == 0


def test_typed_account_evidence_includes_source_only_owner(client, make_case, monkeypatch):
    from app import table_import
    from app.services import imports
    with monkeypatch.context() as configured:
        configured.setattr(imports, "TABLE_PROFILE", table_import.SOURCE_LEDGER_EVIDENCE_PROFILE)
        cid, _, _ = imported(client, make_case)
    page = client.get(f"/api/v1/cases/{cid}/flows").json()
    assert page["total"] == 3
    zero = next(row for row in page["items"] if row["amount"] == "0.00")
    assert zero["source_ledger"]["self_account"] == "000A"
    assert client.get("/api/account-registry/profile", params={"case_id": cid, "account": zero["payer_account"]}).status_code == 200


@pytest.mark.parametrize("selected", [1, 2])
def test_source_only_graph_keeps_explicit_utc_window(client, make_case, selected):
    cid = make_case("SYN source-only UTC view")
    add_source(client, cid, changed_payload([(selected, {"交易时间": "2037-02-03T09:00:01+08:00"})], selected=[selected]))
    row, = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"]]}).status_code == 200
    response = client.post("/api/graph/view", json={"case_id": cid, "start": "2037-02-03T01:00:00Z", "end": "2037-02-03T01:00:02Z"})
    assert response.status_code == 200, response.text
    graph = response.json()
    assert graph["funds"]["observation_count"] == 0 and graph["source_ledger"]["count"] == 1
    assert len(graph["nodes"]) == (2 if selected == 1 else 1)
