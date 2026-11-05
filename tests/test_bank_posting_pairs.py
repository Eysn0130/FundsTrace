"""Independent synthetic bank postings, including contradictions and revocation."""
import csv
from decimal import Decimal
import hashlib
import io
import json
import zipfile

from openpyxl import load_workbook
import pytest

from test_source_contract_68 import HEADERS
from test_source_ledger import add_source


def entries():
    result = []
    for own, other, reference, direction, stamp, amount, balance in [
        ("SYN-A", "SYN-PREV-X", "SYN-PREV-A", "出", "09:00:00", "1.00", "1000.00"),
        ("SYN-A", "SYN-B", "SYN-PAIR", "出", "09:00:01", "37.41", "962.59"),
        ("SYN-B", "SYN-PREV-Y", "SYN-PREV-B", "进", "09:00:00", "1.00", "2000.00"),
        ("SYN-B", "SYN-A", "SYN-PAIR", "进", "09:00:01", "37.41", "2037.41"),
    ]:
        row = dict.fromkeys(HEADERS, "")
        row.update({"交易账号": own, "交易卡号": own, "交易方户名": "SYN owner",
                    "交易对手账卡号": other, "对手开户银行": "SYN Bank",
                    "交易流水号": reference, "收付标志": direction, "交易金额": amount,
                    "交易余额": balance, "交易时间": "2038-02-04 " + stamp, "交易币种": "CNY",
                    "交易是否成功": "01", "查询反馈结果原因": "SYN feedback", "日志号": "0",
                    "传票号": "-", "凭证号": "-"})
        result.append(row)
    return result


def encoded(rows, *, archive=False, headers=HEADERS):
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(headers)
    writer.writerows([row.get(key, "") for key in headers] for row in rows)
    raw = out.getvalue().encode("utf-8-sig")
    if not archive:
        return raw
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("source.csv", raw)
    return out.getvalue()


def targets(client, cid):
    response = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"})
    assert response.status_code == 200, response.text
    return [row for row in response.json()["items"] if not row["trade_no"].startswith("SYN-PREV")]


def tracked_graph(client, cid, rows):
    response = client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"] for row in rows]})
    assert response.status_code == 200, response.text
    response = client.get("/api/graph", params={"case_id": cid})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("archive", [False, True], ids=["csv", "zip"])
def test_paired_postings_count_once_and_retain_both_balances_status_and_proof(client, database, make_case, archive):
    cid = make_case("SYN independently witnessed postings")
    raw = encoded(entries(), archive=archive)
    view, _ = add_source(client, cid, raw, "source.zip" if archive else "source.csv")
    rows = targets(client, cid)
    assert len(rows) == 2
    assert {row["balance"] for row in rows} == {"962.59", "2037.41"}
    single = tracked_graph(client, cid, [rows[int(archive)]])
    assert (single["transaction_count"], single["edges"][0]["total"]) == (1, "37.41")
    graph = tracked_graph(client, cid, rows)
    edge, = graph["edges"]
    assert (graph["transaction_count"], edge["cnt"], edge["total"]) == (1, 1, "37.41")
    stats = client.get(f"/api/v1/cases/{cid}/stats")
    assert stats.status_code == 200, stats.text
    assert (stats.json()["flows"], stats.json()["amount_total"]) == (3, "39.41")
    assert graph["funds"]["status"] == "exact" and graph["funds"]["observation_count"] == 2
    canonical = client.get(f"/api/v1/cases/{cid}/flows", params={"kw": "SYN-PAIR"}).json()
    assert canonical["total"] == 1 and canonical["items"][0]["observation_count"] == 2
    assert canonical["items"][0]["balance"] is None
    sources = client.get(f"/api/v1/cases/{cid}/flows/{canonical['items'][0]['id']}/sources").json()
    assert sources["total"] == 2
    assert {source["observation_balance"] for source in sources["items"]} == {"962.59", "2037.41"}
    assert all(source["record_details"]["reported_status"] == "unknown" for source in sources["items"])
    proof, = database.q_raw("select evidence_json from resolved_events where case_id=?", [cid])
    proof = json.loads(proof["evidence_json"])
    assert proof["policy"] == "bank-feedback31-paired-postings-v1"
    assert proof["source_primary_reference"] == "SYN-PAIR"
    assert proof["source_counterparty_bank"] == "SYN Bank"
    assert len(proof["member_sha256"]) == 64
    assert not {"issuer", "channel", "business_scope", "reference"} & proof.keys()
    for source in sources["items"]:
        assert json.loads(source["record_details"]["current_bank_posting_pair_proof"]) == proof
        assert "event_reference_evidence" not in source["record_details"]
    assert {side["difference"] for side in proof["sides"]} == {"-37.41", "37.41"}
    assert {side["previous_source_row"] for side in proof["sides"]} == {2, 4}
    assert "settlement_and_ultimate_ownership_unproved" in proof["meaning"]
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == raw
    xlsx = client.get("/api/export/tracked.xlsx", params={"case_id": cid})
    assert xlsx.status_code == 200, xlsx.text
    book = load_workbook(io.BytesIO(xlsx.content))
    assert not {"参考号机构", "参考号渠道", "参考号业务域", "跨账簿事件参考号"} & {
        value for sheet in book for row in sheet.values for value in row if isinstance(value, str)}
    observed = list(book["原始账簿观察"].values)
    assert {dict(zip(observed[0], row))["本方原余额"] for row in observed[1:]} == {"962.59", "2037.41"}
    package = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("paired.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    restored_graph = client.get("/api/graph", params={"case_id": restored.json()["case_id"]}).json()
    assert (restored_graph["transaction_count"], restored_graph["edges"][0]["total"]) == (1, "37.41")


@pytest.mark.parametrize("changes,pending", [
    ([(3, {"交易余额": ""})], True), ([(3, {"交易余额": "2037.42"})], True),
    ([(2, {"交易余额": ""})], True), ([(2, {"交易金额": ""})], True), ([(2, {"交易是否成功": "失败"})], True),
    ([(3, {"交易时间": "2038-02-04 09:00:02"})], False),
    ([(2, {"交易时间": "2038-02-04 09:00:01"})], True),
    ([(3, {"交易时间": "2038-02-04T09:00:01+08:00"})], True),
    ([(3, {"对手开户银行": "SYN Other Institution"})], False), ([(3, {"对手开户银行": ""})], False),
    ([(1, {"对手开户银行": "不详"}), (3, {"对手开户银行": "不详"})], False),
    ([(1, {"对手开户银行": "未知"}), (3, {"对手开户银行": "未知"})], False),
    # Different/missing primary IDs do not prove a merge; v2 also withholds
    # certainty when a different owner may be the opposite source observation.
    ([(3, {"交易流水号": "SYN independent transaction"})], True), ([(3, {"交易流水号": "-"})], True),
    ([(3, {"日志号": "SYN independent log"})], False), ([(3, {"凭证号": "SYN independent voucher"})], False),
    ([(3, {"凭证号": "SYN-" + "V" * 129})], False),
    ([(3, {"交易卡号": "SYN different subledger card"})], True),
])
def test_missing_or_conflicting_pair_evidence_keeps_two_observations(client, make_case, changes, pending):
    rows = entries()
    for index, change in changes:
        rows[index].update(change)
    cid = make_case("SYN non-pairing evidence")
    add_source(client, cid, encoded(rows))
    graph = tracked_graph(client, cid, targets(client, cid))
    assert graph["transaction_count"] == 2
    assert graph["funds"]["confirmed_event_count"] == 0 and graph["funds"]["observation_count"] == 2
    assert graph["funds"]["totals"] == [{"currency": "CNY", "amount": None, "lower": "0.00", "upper": "74.82"}]
    assert graph["edges"][0]["total"] == "74.82"
    assert bool(graph["edges"][0].get("pending_observation_count")) is pending
    assert not graph["edges"][0].get("time_basis_pending")


@pytest.mark.parametrize("previous_amount", ["0.00", "-1.00"])
def test_nonfinancial_preceding_balance_is_a_source_observation_not_an_event(client, make_case, previous_amount):
    rows = entries()
    rows[2]["交易金额"] = previous_amount
    cid = make_case("SYN source-only preceding balance witness")
    add_source(client, cid, encoded(rows))
    graph = tracked_graph(client, cid, targets(client, cid))
    assert (graph["transaction_count"], graph["edges"][0]["total"]) == (1, "37.41")
    sources = client.get(f"/api/v1/cases/{cid}/flows/{targets(client, cid)[0]['id']}/sources").json()
    proof = json.loads(sources["items"][0]["record_details"]["current_bank_posting_pair_proof"])
    side = next(side for side in proof["sides"] if side["own_account"] == "SYN-B")
    assert (side["previous_source_row"], side["before"], side["difference"]) == (4, "2000.00", "37.41")


@pytest.mark.parametrize("same_reference", [False, True], ids=["independent-references", "reused-reference"])
@pytest.mark.parametrize("amount,first_balance,second_balance,total", [
    ("37.41", "962.59", "925.18", "74.82"), ("144.00", "856.00", "712.00", "288.00"),
])
def test_legitimate_same_amount_debits_and_reused_reference_are_not_paired(client, make_case, same_reference, amount, first_balance, second_balance, total):
    rows = entries()
    rows[1].update({"交易金额": amount, "交易余额": first_balance})
    rows[3].update({"交易账号": "SYN-A", "交易卡号": "SYN-A", "交易对手账卡号": "SYN-B",
                    "收付标志": "出", "交易时间": "2038-02-04 09:00:02", "交易金额": amount, "交易余额": second_balance})
    if not same_reference:
        rows[3]["交易流水号"] = "SYN independent transaction"
    cid = make_case("SYN two legitimate same-amount debits")
    add_source(client, cid, encoded(rows))
    graph = tracked_graph(client, cid, targets(client, cid))
    assert (graph["transaction_count"], graph["edges"][0]["total"]) == (2, total)
    assert graph["funds"]["confirmed_event_count"] == (0 if same_reference else 2)
    assert graph["funds"]["totals"][0]["amount"] == (None if same_reference else total)


@pytest.mark.parametrize("selection", ["both", "debit", "credit", "with-known-event"])
def test_balance_ambiguity_keeps_observations_and_ranges_without_certain_money(client, database, make_case, selection):
    from test_event_import import csv_rows, transaction
    from test_folder_evidence import accepted
    cid = make_case("SYN one-cent ambiguous pair")
    rows = entries()
    rows[0]["交易余额"] = "1000.01"
    add_source(client, cid, encoded(rows))
    observed = targets(client, cid)
    chosen = [row for row in observed if selection not in {"debit", "credit"}
              or row["balance"] == ("962.59" if selection == "debit" else "2037.41")]
    if selection == "with-known-event":
        known = [transaction(own, **{"付款账号": "SYN-C", "收款账号": "SYN-D", "交易金额": "11.13",
                    "跨账簿事件参考号": "SYN-KNOWN-EVENT", "交易流水号": "SYN-KNOWN-" + own})
                 for own in ("SYN-C", "SYN-D")]
        accepted(client, cid, [("known.csv", csv_rows(known))], scope_bank=None)
        extra = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations", "kw": "SYN-KNOWN-"}).json()["items"]
        assert len(extra) == 2
        chosen.append(extra[0])
    graph = tracked_graph(client, cid, chosen)
    ambiguous = next(edge for edge in graph["edges"] if edge.get("pending_pair_observation_count"))
    expected_observations = 1 if selection in {"debit", "credit"} else 2
    expected_amount = "37.41" if expected_observations == 1 else "74.82"
    assert graph["transaction_count"] == len(chosen) and ambiguous["cnt"] == expected_observations and ambiguous["total"] == expected_amount
    stats = client.get(f"/api/v1/cases/{cid}/stats")
    assert stats.status_code == 200, stats.text
    assert stats.json()["flows"] == (5 if selection == 'with-known-event' else 4)
    assert stats.json()["amount_total"] == ('87.95' if selection == 'with-known-event' else '76.82')
    assert ambiguous["amount_totals"] == [{"currency": "CNY", "amount": expected_amount}]
    assert ambiguous["pending_observation_count"] == expected_observations
    assert ambiguous["funds"]["observation_count"] == expected_observations and ambiguous["funds_pending"] is True
    assert not ambiguous.get("time_basis_pending")
    assert ambiguous["funds"]["totals"] == [{"currency": "CNY", "amount": None, "lower": "0.00", "upper": "37.41" if expected_observations == 1 else "74.82"}]
    if selection == "with-known-event":
        unrelated, = [edge for edge in graph["edges"] if not edge.get("pending_pair_observation_count")]
        assert (unrelated["cnt"], unrelated["total"], unrelated["funds"]["status"]) == (1, "11.13", "exact")
        assert not unrelated.get("pending_observation_count")
    filtered = client.get("/api/flows", params={"case_id": cid, "ids": ",".join(row["id"] for row in chosen[:expected_observations]),
                          "min_amount": "37.41", "max_amount": "37.41"}).json()
    assert len(filtered["rows"]) == expected_observations
    assert all(row["amount"] == "37.41" and row["fact_pair_pending"] for row in filtered["rows"])
    assert filtered["funds"]["totals"][0]["amount"] is None
    sources = client.get(f"/api/v1/cases/{cid}/flows/{observed[0]['id']}/sources").json()
    assert sources["total"] == 2
    assert {source["observation_balance"] for source in sources["items"]} == {"962.59", "2037.41"}
    assert all(source["record_details"].get("current_bank_posting_pair_pending")
               and "current_bank_posting_pair_proof" not in source["record_details"] for source in sources["items"])
    book = load_workbook(io.BytesIO(client.get("/api/export/tracked.xlsx", params={"case_id": cid}).content))
    main = list(book["追踪流水"].values)
    projected = [dict(zip(main[0], row)) for row in main[1:]]
    pending = [row for row in projected if row["资金关联说明"]]
    assert len(pending) == expected_observations and all(row["交易金额"] == '37.41' for row in pending)
    original = list(book["原始账簿观察"].values)
    original = [dict(zip(original[0], row)) for row in original[1:]]
    assert sum(row["观察金额"] == "37.41" for row in original) == expected_observations
    if selection == "with-known-event":
        assert [row["交易金额"] for row in projected if not row["资金关联说明"]] == ["11.13"]
    if selection == "both":
        package = client.get(f"/api/cases/{cid}/export")
        restored = client.post("/api/cases/import", files={"file": ("pending.fundcase", package.content)})
        assert restored.status_code == 200, restored.text
        reopened = client.get("/api/graph", params={"case_id": restored.json()["case_id"]}).json()
        assert reopened["transaction_count"] == 2 and reopened["edges"][0]["total"] == '74.82'
        assert reopened["funds"] == graph["funds"]
    decisions = database.q_raw("select event_id,status,pending_group from event_members where case_id=? and pending_group is not null", [cid])
    assert len(decisions) == 2 and all(row["event_id"] is None and row["status"] == "unknown" for row in decisions)


def test_currencies_and_members_do_not_create_cross_source_pair_authority(client, make_case):
    rows = entries()
    rows[3]["交易币种"] = "USD"
    cid = make_case("SYN currency contradiction")
    add_source(client, cid, encoded(rows))
    graph = tracked_graph(client, cid, targets(client, cid))
    assert graph["transaction_count"] == 2 and graph["funds"]["confirmed_event_count"] == 0
    assert graph["edges"][0]["amount_totals"] == [{"currency": "CNY", "amount": "37.41"}, {"currency": "USD", "amount": "37.41"}]
    cid = make_case("SYN separate source members")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("a.csv", encoded(entries()[:2]))
        z.writestr("b.csv", encoded(entries()[2:]))
    add_source(client, cid, out.getvalue(), "separate.zip")
    graph = tracked_graph(client, cid, targets(client, cid))
    assert (graph["transaction_count"], graph["edges"][0]["total"]) == (2, "74.82")


def test_generic_primary_field_and_nonreciprocal_parties_do_not_gain_bank_pair_authority(client, make_case):
    rows = entries()
    for row in rows:
        row["本方账号"] = row["交易账号"]
    generic = ["交易时间", "本方账号", "交易对手账卡号", "交易金额", "交易余额", "收付标志", "交易币种", "交易流水号", "对手开户银行"]
    cid = make_case("SYN unregistered source family")
    add_source(client, cid, encoded(rows, headers=generic))
    graph = tracked_graph(client, cid, targets(client, cid))
    assert (graph["transaction_count"], graph["edges"][0]["total"]) == (2, "74.82")
    rows = entries()
    rows[3]["交易对手账卡号"] = "SYN-C"
    cid = make_case("SYN nonreciprocal accounts")
    add_source(client, cid, encoded(rows))
    graph = tracked_graph(client, cid, targets(client, cid))
    assert graph["transaction_count"] == 2 and graph["funds"]["confirmed_event_count"] == 0
    assert {(edge["source"], edge["target"], edge["total"]) for edge in graph["edges"]} == {
        ("SYN-A", "SYN-B", "37.41"), ("SYN-C", "SYN-B", "37.41")}


def test_late_third_row_revokes_pair_outside_canvas_and_withdrawal_reassesses(client, make_case):
    cid = make_case("SYN mutable posting proof")
    add_source(client, cid, encoded(entries()))
    original = targets(client, cid)
    assert tracked_graph(client, cid, original)["edges"][0]["total"] == "37.41"
    third = dict(entries()[1])
    third.update({"交易时间": "2038-02-04 09:00:02", "交易余额": "925.18"})
    other = make_case("SYN isolated competing reference")
    add_source(client, other, encoded([third]), "other-case.csv")
    assert client.get("/api/graph", params={"case_id": cid}).json()["edges"][0]["total"] == "37.41"
    _, late = add_source(client, cid, encoded([third]), "late.csv")
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert (graph["transaction_count"], graph["edges"][0]["total"]) == (2, '74.82')
    assert graph["funds"]["confirmed_event_count"] == 0
    for row in original:
        sources = client.get(f"/api/v1/cases/{cid}/flows/{row['id']}/sources").json()
        assert all("current_bank_posting_pair_proof" not in source["record_details"] for source in sources["items"])
        assert all(source["record_details"].get("current_bank_posting_pair_pending") for source in sources["items"])
    assert graph["funds"]["totals"][0]["upper"] == "74.82"  # Observation upper bound, not certain money.
    response = client.delete(f"/api/batches/{late['result']['batch_id']}", params={"case_id": cid})
    assert response.status_code == 200, response.text
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert (graph["transaction_count"], graph["edges"][0]["total"]) == (1, "37.41")
    sources = client.get(f"/api/v1/cases/{cid}/flows/{original[0]['id']}/sources").json()
    assert all("current_bank_posting_pair_proof" in source["record_details"]
               and "current_bank_posting_pair_pending" not in source["record_details"] for source in sources["items"])


@pytest.mark.parametrize("second_archive", [False, True], ids=["repeat-csv", "same-member-in-zip"])
def test_repeat_import_and_proof_support_withdrawal_keep_observations_and_exact_money(client, make_case, second_archive):
    cid = make_case("SYN duplicate posting support")
    _, first = add_source(client, cid, encoded(entries()))
    rows = targets(client, cid)
    before = tracked_graph(client, cid, rows)
    _, second = add_source(client, cid, encoded(entries(), archive=second_archive), "source.zip" if second_archive else "source.csv")
    assert {row["id"] for row in targets(client, cid)} == {row["id"] for row in rows}
    for job, expected in ((first, 1), (second, 0)):
        response = client.delete(f"/api/batches/{job['result']['batch_id']}", params={"case_id": cid})
        assert response.status_code == 200, response.text
        graph = client.get("/api/graph", params={"case_id": cid}).json()
        assert graph["transaction_count"] == expected
        if expected:
            assert graph["funds"] == before["funds"] and graph["edges"][0]["total"] == "37.41"


def test_old_ready_parser_replays_old_interpretation_until_explicit_new_import(client, make_case, monkeypatch):
    from app import table_import
    from app.services import imports
    from test_automatic_table_import import preview
    from test_folder_evidence import complete
    cid = make_case("SYN versioned bank posting interpretation")
    raw = encoded(entries())
    with monkeypatch.context() as old:
        old.setattr(imports, "TABLE_PROFILE", table_import.SOURCE_LEDGER_PROFILE)
        view = preview(client, cid, raw)
    assert complete(client, cid, view)["stage"] == "done"
    receipt = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json()
    rows = targets(client, cid)
    assert tracked_graph(client, cid, rows)["edges"][0]["total"] == "74.82"
    add_source(client, cid, raw)
    assert client.get("/api/graph", params={"case_id": cid}).json()["edges"][0]["total"] == "37.41"
    assert client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json() == receipt


def test_registered_posting_repetitions_keep_one_pair_with_original_rows(client, make_case):
    rows = entries()
    rows += [dict(rows[1]), dict(rows[3])]
    cid = make_case("SYN repeated posting rows")
    view, _ = add_source(client, cid, encoded(rows))
    graph = tracked_graph(client, cid, targets(client, cid))
    assert (graph["transaction_count"], graph["edges"][0]["total"]) == (1, "37.41")
    report = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()
    assert report["total"] == 6


@pytest.mark.parametrize("independent_field", ["日志号", "凭证号"])
def test_repeated_support_cannot_hide_a_retained_independent_reference(client, make_case, independent_field):
    rows = entries()
    repeat = dict(rows[1])
    repeat[independent_field] = "SYN independent evidence"
    rows.append(repeat)
    cid = make_case("SYN independent reference on repeated support")
    add_source(client, cid, encoded(rows))
    graph = tracked_graph(client, cid, targets(client, cid))
    assert graph["transaction_count"] == 2 and graph["edges"][0]["total"] == '74.82'
    assert graph["funds"]["confirmed_event_count"] == 0


@pytest.mark.parametrize("field,value", [("source_primary_reference", "SYN forged reference"), ("source_counterparty_bank", "SYN Other Institution"),
                                         ("source_fields", "null"), ("source_fields", "{}"), ("source_fields", "[42]")])
def test_package_rejects_pair_contract_that_disagrees_with_retained_source(client, make_case, tmp_path, field, value):
    from test_feedback_package import _members, _mutate, _archive
    cid = make_case("SYN portable posting proof admission")
    view, _ = add_source(client, cid, encoded(entries()))
    details = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"][1]["details"]
    contract = json.loads(details["bank_posting_pair_evidence"])
    if field == "source_fields":
        details["source_field_evidence"] = value
    else:
        contract[field] = value
        details["bank_posting_pair_evidence"] = json.dumps(contract)
    text = json.dumps(details, ensure_ascii=False).replace("'", "''")
    members = _members(client.get(f"/api/cases/{cid}/export").content)
    _mutate(members, "import_records", f"update entries set details_json='{text}' where record_no=2", tmp_path)
    response = client.post("/api/cases/import", files={"file": ("tampered.fundcase", _archive(members))})
    assert response.status_code == 400, response.text


def test_multiple_bank_event_revisions_use_stable_ids_without_invented_issuer_scope(client, database, make_case):
    from app import bank_posting_pairs, event_resolution
    cid = make_case("SYN independent source pair revisions")
    for suffix in ("", "-SECOND"):
        rows = entries()
        for row in rows:
            for key in ("交易账号", "交易卡号", "交易对手账卡号", "交易流水号"):
                row[key] += suffix
        add_source(client, cid, encoded(rows), "source" + suffix + ".csv")
    summary = event_resolution.summary(cid)
    revision = summary.pop("revision")
    resolved, memberships, _ = bank_posting_pairs.project(cid)
    assert len(resolved) == 2 and summary["confirmed_events"] == 2
    canonical = [{key: str(value) if isinstance(value, Decimal) or hasattr(value, "isoformat") else value
                  for key, value in event.items()} for event in sorted(resolved, key=lambda event: event["event_id"])]
    memberships.sort(key=lambda member: member["flow_id"])
    assert revision == hashlib.sha256(event_resolution._json([summary, canonical, memberships]).encode()).hexdigest()
    with database._lock:
        assert event_resolution.rebuild(cid, force_full=True)["revision"] == revision


def test_earlier_bank_draft_reopens_with_rebuilt_pending_fields_and_unchanged_sources(client, database, make_case):
    from app import event_resolution
    cid = make_case("SYN earlier derived bank schema")
    rows = entries()
    rows[0]["交易余额"] = "1000.01"
    view, _ = add_source(client, cid, encoded(rows))
    observed = targets(client, cid)
    tracked_graph(client, cid, observed)
    receipt = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json()
    before = database.q_raw("select * from import_records where case_id=? order by record_no", [cid])
    old_summary = event_resolution.summary(cid)
    old_summary["policy"] = "scoped-source-postings-v2"
    with database._lock:
        database.x("update event_state set summary_json=? where case_id=?", [json.dumps(old_summary), cid])
        database.con().execute("alter table event_members drop column pending_group")
        database.con().execute("checkpoint")
        database._con.close()
        database._con = None
        database.con()
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert graph["transaction_count"] == 2 and graph["edges"][0]["total"] == '74.82'
    assert graph["edges"][0]["pending_observation_count"] == 2
    assert graph["case_event_summary"]["policy"] == event_resolution.BANK_SUMMARY_POLICY
    assert database.q_raw("select * from import_records where case_id=? order by record_no", [cid]) == before
    assert client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json() == receipt
    revision = graph["case_event_summary"]["revision"]
    event_resolution.ensure_bank_projection()
    assert event_resolution.summary(cid)["revision"] == revision


def test_failed_late_import_rolls_back_pending_members_with_source_facts(client, database, make_case, monkeypatch):
    from test_automatic_table_import import preview
    from test_folder_evidence import complete
    cid = make_case("SYN atomic pending projection")
    add_source(client, cid, encoded(entries()))
    observed = targets(client, cid)
    before_graph = tracked_graph(client, cid, observed)
    before = database.q_raw("select * from event_members where case_id=? order by flow_id", [cid])
    third = dict(entries()[1])
    third.update({"交易时间": "2038-02-04 09:00:02", "交易余额": "925.18"})
    view = preview(client, cid, encoded([third]), "late.csv")
    writer = database.insert_dataframe
    def fail_after_projection(table, frame, columns):
        result = writer(table, frame, columns)
        if table == "event_members":
            raise RuntimeError("SYN pending projection failure")
        return result
    monkeypatch.setattr(database, "insert_dataframe", fail_after_projection)
    assert complete(client, cid, view)["stage"] == "error"
    assert database.q_raw("select * from event_members where case_id=? order by flow_id", [cid]) == before
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert (graph["transaction_count"], graph["edges"][0]["total"], graph["funds"]) == (1, "37.41", before_graph["funds"])


def test_member_primary_reuse_is_not_split_by_counterparty_bank_and_old_confirmation_is_revoked(client, database, make_case):
    from app import event_resolution
    cid = make_case("SYN same-member primary namespace")
    rows = entries()
    rows[0].update({"交易流水号": "SYN-PAIR", "交易金额": "23.54", "对手开户银行": "SYN Context Bank"})
    view, _ = add_source(client, cid, encoded(rows))
    observed = targets(client, cid)
    paired = [row for row in observed if row["amount"] == "37.41"]
    context, = [row for row in observed if row["amount"] == "23.54"]
    graph = tracked_graph(client, cid, observed)
    assert graph["funds"]["confirmed_event_count"] == 0
    pending = next(edge for edge in graph["edges"] if edge.get("pending_pair_observation_count"))
    assert pending["cnt"] == 2 and pending["total"] == '74.82'
    separate = next(edge for edge in graph["edges"] if str(context["id"]) in set(map(str, edge["ids"])))
    assert (separate["cnt"], separate["total"]) == (1, "23.54")
    receipt = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json()
    records = database.q_raw("select * from import_records where case_id=? order by record_no", [cid])
    # Reproduce the earlier draft's persisted wrong projection, retaining all source facts.
    old_summary = event_resolution.summary(cid)
    old_summary["policy"] = "scoped-source-postings-v3"
    with database.transaction():
        database.x("insert into resolved_events(case_id,event_id,payer_account,payee_account,currency,amount,evidence_json) values (?,'SYN-OLD-WRONG','SYN-A','SYN-B','CNY',37.41,'{}')", [cid])
        database.x("update event_members set event_id='SYN-OLD-WRONG',status='confirmed',pending_group=null where case_id=? and flow_id in (select unnest(?::bigint[]))", [cid, [int(row["id"]) for row in paired]])
        database.x("update event_state set summary_json=? where case_id=?", [json.dumps(old_summary), cid])
    with database._lock:
        database.con().execute("checkpoint")
        database._con.close()
        database._con = None
        database.con()
    reopened = client.get("/api/graph", params={"case_id": cid}).json()
    assert reopened["funds"]["confirmed_event_count"] == 0
    assert reopened["case_event_summary"]["policy"] == event_resolution.BANK_SUMMARY_POLICY
    assert database.q_raw("select * from import_records where case_id=? order by record_no", [cid]) == records
    assert client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json() == receipt
