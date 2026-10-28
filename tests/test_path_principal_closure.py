"""Existing canvas paths share a complete first-exit recorded-book model."""
import pytest

from test_event_import import csv_rows, transaction
from test_folder_evidence import accepted


def posting(own, payer, payee, reference, second, amount, balance, *, currency="CNY"):
    return transaction(own, **{"付款账号": payer, "收款账号": payee,
        "交易流水号": reference + "-" + own, "跨账簿事件参考号": reference,
        "交易时间": f"2026-01-01 10:00:{second:02d}", "交易金额": amount,
        "本笔交易后账面余额": balance, "交易币种": currency, "记账顺序号": str(second + 1)})


def internal(payer, payee, reference, second, amount, payer_balance, payee_balance):
    return [posting(payer, payer, payee, reference, second, amount, payer_balance),
            posting(payee, payer, payee, reference, second, amount, payee_balance)]


def seed(client, cid, rows, tracked_references):
    accepted(client, cid, [("path-book.csv", csv_rows(rows))], scope_bank=None)
    observations = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()["items"]
    keys = {row["raw_self_account"]: row["self_account"] for row in observations}
    ids = [row["id"] for row in observations if any(row["trade_no"].startswith(reference + "-") for reference in tracked_references)]
    response = client.post("/api/track", json={"case_id": cid, "flow_ids": ids})
    assert response.status_code == 200, response.text
    return keys, observations


def paths(client, cid, *, mode="chronological", **scope):
    response = client.post("/api/canvas/investigation/paths", json={"case_id": cid,
        "start_account": "SYN-X", "end_account": "SYN-Z", "mode": mode, **scope})
    assert response.status_code == 200, response.text
    return response.json()


def allocation(value):
    assert len(value["paths"]) == 1, value
    result = value["paths"][0]["principal"]
    assert result["revision"] == value["analysis_revision"]
    assert result["model_scope"]["boundary"] == "first_exit"
    return result


def target(value):
    result = allocation(value)
    assert result["status"] == "ok", result
    assert len(result["source_candidates"]) == 1
    candidate = result["source_candidates"][0]
    assert candidate["status"] == "ok", candidate
    assert len(candidate["targets"]) == 1
    return candidate, candidate["targets"][0]


def test_untracked_split_competitor_constrains_existing_path_and_keeps_relation_money(client, make_case):
    cid = make_case("Synthetic path split competitor")
    rows = [posting("SYN-B", "SYN-X", "SYN-B", "SYN-SOURCE", 0, "10.00", "20.00"),
            *internal("SYN-B", "SYN-C", "SYN-BC", 1, "10.00", "10.00", "10.00"),
            posting("SYN-B", "SYN-B", "SYN-Y", "SYN-COMPETITOR", 2, "10.00", "0.00"),
            posting("SYN-C", "SYN-C", "SYN-Z", "SYN-EXIT", 3, "10.00", "0.00")]
    seed(client, cid, rows, ["SYN-SOURCE", "SYN-BC", "SYN-EXIT"])
    value = paths(client, cid)
    candidate, last = target(value)
    assert (last["min"], last["max"], last["fifo"]) == ("0.00", "10.00", "0.00")
    assert candidate["ledger_count"] == 5
    assert value["paths"][0]["steps"][-1]["funds"]["totals"][0]["upper"] == "10.00"
    assert candidate["fifo_witness"]["source_amount"] == "10.00"


def test_untracked_internal_merge_and_non_simple_return_share_one_network(client, make_case):
    cid = make_case("Synthetic path internal return")
    rows = [posting("SYN-B", "SYN-X", "SYN-B", "SYN-SOURCE", 0, "10.00", "10.00"),
            *internal("SYN-B", "SYN-C", "SYN-BC-EARLY", 1, "10.00", "0.00", "10.00"),
            *internal("SYN-C", "SYN-B", "SYN-RETURN", 2, "10.00", "0.00", "10.00"),
            *internal("SYN-B", "SYN-C", "SYN-BC-LATE", 3, "10.00", "0.00", "10.00"),
            posting("SYN-C", "SYN-C", "SYN-Z", "SYN-EXIT", 4, "10.00", "0.00")]
    seed(client, cid, rows, ["SYN-SOURCE", "SYN-BC-LATE", "SYN-EXIT"])
    candidate, last = target(paths(client, cid))
    assert (last["min"], last["max"], last["fifo"]) == ("10.00",) * 3
    assert candidate["event_count"] == 5
    assert candidate["fifo_witness"]["external_total"] == "10.00"
    assert candidate["fifo_witness"]["retained_total"] == "0.00"


def test_untracked_internal_diamond_merge_does_not_sum_per_hop_extrema(client, make_case):
    cid = make_case("Synthetic path diamond merge")
    rows = [posting("SYN-B", "SYN-X", "SYN-B", "SYN-SOURCE", 0, "10.00", "10.00"),
            *internal("SYN-B", "SYN-C", "SYN-DIRECT", 1, "6.00", "4.00", "6.00"),
            *internal("SYN-B", "SYN-D", "SYN-BD", 2, "4.00", "0.00", "4.00"),
            *internal("SYN-D", "SYN-C", "SYN-DC", 3, "4.00", "0.00", "10.00"),
            posting("SYN-C", "SYN-C", "SYN-Z", "SYN-EXIT", 4, "10.00", "0.00")]
    seed(client, cid, rows, ["SYN-SOURCE", "SYN-BD", "SYN-DC", "SYN-EXIT"])
    candidate, last = target(paths(client, cid))
    assert (last["min"], last["max"], last["fifo"]) == ("10.00",) * 3
    assert candidate["source_amount"] == candidate["fifo_witness"]["external_total"] == "10.00"


def test_external_exit_and_return_do_not_restore_the_selected_source(client, make_case):
    cid = make_case("Synthetic path external return")
    rows = [posting("SYN-B", "SYN-X", "SYN-B", "SYN-SOURCE", 0, "10.00", "10.00"),
            posting("SYN-B", "SYN-B", "SYN-Y", "SYN-OUTSIDE", 1, "10.00", "0.00"),
            posting("SYN-B", "SYN-Y", "SYN-B", "SYN-RETURN", 2, "10.00", "10.00"),
            *internal("SYN-B", "SYN-C", "SYN-BC", 3, "10.00", "0.00", "10.00"),
            posting("SYN-C", "SYN-C", "SYN-Z", "SYN-EXIT", 4, "10.00", "0.00")]
    seed(client, cid, rows, ["SYN-SOURCE", "SYN-BC", "SYN-EXIT"])
    candidate, last = target(paths(client, cid))
    assert (last["min"], last["max"], last["fifo"]) == ("0.00",) * 3
    assert "external_boundary_returns_unknown" in candidate["limitations"]
    assert candidate["fifo_witness"]["external_total"] == "10.00"


@pytest.mark.parametrize("references", [True, False])
def test_opposite_observation_maps_source_only_with_confirmed_event(client, make_case, references):
    cid = make_case("Synthetic path opposite source " + str(references))
    rows = [*internal("SYN-X", "SYN-B", "SYN-SOURCE", 0, "10.00", "0.00", "10.00"),
            *internal("SYN-B", "SYN-C", "SYN-BC", 1, "10.00", "0.00", "10.00"),
            posting("SYN-C", "SYN-C", "SYN-Z", "SYN-EXIT", 2, "10.00", "0.00")]
    if not references:
        for row in rows:
            for field in ("参考号机构", "参考号渠道", "参考号业务域", "跨账簿事件参考号"):
                row.pop(field)
    _keys, observed = seed(client, cid, rows, ["SYN-BC", "SYN-EXIT"])
    payer = next(row for row in observed if row["trade_no"] == "SYN-SOURCE-SYN-X")
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [payer["id"]]}).status_code == 200
    value = paths(client, cid)
    if references:
        candidate, last = target(value)
        assert candidate["source_flow_id"] != payer["id"]
        assert candidate["model_scope"]["boundary"] == "first_exit"
        assert last["max"] == "10.00"
    else:
        # Without event evidence, both BC observations remain separate path
        # alternatives. Neither may invent the source's opposite own entry.
        assert len(value["paths"]) == 2
        for path in value["paths"]:
            result = path["principal"]
            assert result["status"] == "unknown" and result["source_candidates"] == []
            assert result["reasons"] == ["source_own_entry_not_established"]


def test_topological_multiple_first_postings_remain_independent_sources(client, make_case):
    cid = make_case("Synthetic path distinct source candidates")
    rows = [posting("SYN-B", "SYN-X", "SYN-B", "SYN-SOURCE-ONE", 0, "10.00", "10.00"),
            posting("SYN-B", "SYN-X", "SYN-B", "SYN-SOURCE-TWO", 1, "10.00", "20.00"),
            *internal("SYN-B", "SYN-C", "SYN-BC", 2, "20.00", "0.00", "20.00"),
            posting("SYN-C", "SYN-C", "SYN-Z", "SYN-EXIT", 3, "20.00", "0.00")]
    seed(client, cid, rows, ["SYN-SOURCE-ONE", "SYN-SOURCE-TWO", "SYN-BC", "SYN-EXIT"])
    result = allocation(paths(client, cid, mode="topological"))
    assert result["status"] == "ok" and len(result["source_candidates"]) == 2
    assert len({candidate["source_flow_id"] for candidate in result["source_candidates"]}) == 2
    assert {candidate["targets"][0]["max"] for candidate in result["source_candidates"]} == {"10.00"}
    assert result["candidate_semantics"] == "independent_conditional_source_shares"


@pytest.mark.parametrize("references", [True, False])
def test_opposite_last_observation_requires_confirmed_own_debit(client, make_case, references):
    cid = make_case("Synthetic path opposite target " + str(references))
    last = internal("SYN-C", "SYN-Z", "SYN-EXIT", 2, "10.00", "0.00", "10.00")
    if not references:
        for row in last:
            for field in ("参考号机构", "参考号渠道", "参考号业务域", "跨账簿事件参考号"):
                row.pop(field)
    rows = [posting("SYN-B", "SYN-X", "SYN-B", "SYN-SOURCE", 0, "10.00", "10.00"),
            *internal("SYN-B", "SYN-C", "SYN-BC", 1, "10.00", "0.00", "10.00"), *last]
    _keys, observed = seed(client, cid, rows, ["SYN-SOURCE", "SYN-BC"])
    recipient = next(row for row in observed if row["trade_no"] == "SYN-EXIT-SYN-Z")
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [recipient["id"]]}).status_code == 200
    value = paths(client, cid)
    if references:
        candidate, last = target(value)
        assert last["max"] == "10.00"
        assert last["flow_ids"] != [recipient["id"]]
        assert list(candidate["target_observation_ids"].values()) == [[recipient["id"]]]
    else:
        result = allocation(value)
        assert result["status"] == "unknown" and result["source_candidates"] == []
        assert result["reasons"] == ["target_own_entry_not_established"]


def test_network_call_budget_is_shared_across_paths_without_partial_candidates(client, make_case, monkeypatch):
    from app import principal_routes
    cid = make_case("Synthetic shared path attribution budget")
    rows = [posting("SYN-B", "SYN-X", "SYN-B", f"SYN-SOURCE-{index}", index,
                    "1.00", f"{index + 1}.00") for index in range(21)]
    rows += [*internal("SYN-B", "SYN-C", "SYN-BC", 21, "21.00", "0.00", "21.00"),
             posting("SYN-C", "SYN-C", "SYN-Z", "SYN-EXIT", 22, "21.00", "0.00")]
    seed(client, cid, rows, [*[f"SYN-SOURCE-{index}" for index in range(21)], "SYN-BC", "SYN-EXIT"])
    calls, calculate = [], principal_routes.calculate_network

    def observed(request):
        calls.append(request.source_flow_id)
        return calculate(request)

    monkeypatch.setattr(principal_routes, "calculate_network", observed)
    value = paths(client, cid, max_paths=30)
    assert len(value["paths"]) == 21 and len(calls) == 20
    limited = [path["principal"] for path in value["paths"] if path["principal"]["status"] == "limited"]
    assert len(limited) == 1
    assert limited[0]["source_candidates"] == []
    assert limited[0]["reasons"] == ["path_principal_budget_limit"]


def test_path_window_and_revision_apply_to_network_without_hiding_unknown_competitors(client, make_case):
    cid = make_case("Synthetic path scope revision")
    rows = [posting("SYN-B", "SYN-X", "SYN-B", "SYN-SOURCE", 0, "10.00", "10.00"),
            *internal("SYN-B", "SYN-C", "SYN-BC", 1, "10.00", "0.00", "10.00"),
            posting("SYN-C", "SYN-C", "SYN-Z", "SYN-EXIT", 2, "10.00", "0.00")]
    seed(client, cid, rows, ["SYN-SOURCE", "SYN-BC", "SYN-EXIT"])
    before = paths(client, cid, start="2026-01-01T10:00:00", end="2026-01-01T10:00:02")
    candidate, _ = target(before)
    assert candidate["interval"] == {"start": "2026-01-01T10:00:00", "end": "2026-01-01T10:00:02"}
    usd = posting("SYN-B", "SYN-Y", "SYN-B", "SYN-USD", 1, "500.00", "500.00", currency="USD")
    accepted(client, cid, [("usd-book.csv", csv_rows([usd]))], scope_bank=None)
    separate = paths(client, cid)
    assert target(separate)[1]["max"] == "10.00"
    assert before["analysis_revision"] != separate["analysis_revision"]
    extra = posting("SYN-B", "SYN-Y", "SYN-B", "SYN-UNKNOWN", 1, "1.00", "1.00", currency="unknown")
    # Admission retains this financial row as unresolved. It blocks the model
    # rather than disappearing from its CNY capacity because it is untracked.
    accepted(client, cid, [("unknown-book.csv", csv_rows([extra]))], scope_bank=None)
    after = paths(client, cid)
    assert before["analysis_revision"] != after["analysis_revision"]
    result = allocation(after)
    assert result["status"] == "unknown"
    assert result["source_candidates"][0]["targets"] == []
    assert result["source_candidates"][0]["reasons"] == ["unresolved_source_records"]


def test_one_relationship_has_no_internal_principal_model(client, make_case):
    cid = make_case("Synthetic direct path no internal model")
    seed(client, cid, [posting("SYN-Z", "SYN-X", "SYN-Z", "SYN-DIRECT", 0, "10.00", "10.00")], ["SYN-DIRECT"])
    result = allocation(paths(client, cid))
    assert result["status"] == "unknown" and result["source_candidates"] == []
    assert result["reasons"] == ["path_has_no_internal_ledger"]
