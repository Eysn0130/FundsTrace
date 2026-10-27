"""Public exact case statistics and their same-read coverage counting contract."""
from decimal import Decimal

import pytest

from app import table_import
from app.import_coverage import import_coverage
from app.services import cases
from funds_assertions import unresolved_funds
from test_archive_profile_upgrade import frozen_preview
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_cancellations import round_trip, source
from test_table_contract_lifecycle import source_records
from test_table_mapping_versions import profile_preview


V3 = "funds-trace-table-archive-v3"
V4 = "funds-trace-table-archive-v4"


def coverage_value(*, unresolved=0, non_monetary=0, cancelled=0, excluded=0):
    return {"scope": "source_reported_flows", "unresolved_records": unresolved,
            "non_monetary_records": non_monetary, "cancelled_records": cancelled,
            "excluded_flows": excluded, "financial_interpretation_complete": unresolved == 0}


def stats_value(cid, *, observations, accounts, batches, bounds, coverage, resolution=None):
    # Each fixture supplies literal observation counts and independent bounds;
    # neither an order number nor a source row establishes a confirmed event.
    funds = unresolved_funds(observations, *bounds)
    return {"case_id": str(cid), "flows": None if observations else 0, "accounts": accounts, "batches": batches,
            "amount_total": None if bounds else "0.00",
            "currency": bounds[0][0] if len(bounds) == 1 else None,
            "amount_totals": [{"currency": code, "amount": "0.00" if upper == "0.00" else None}
                              for code, upper in bounds],
            "funds": funds, "statistics_policy": "evidence-bounded-funds-v1",
            "resolution": resolution, "resolution_revision": resolution["revision"] if resolution else None,
            "import_coverage": coverage}


def assert_stats(client, cid, **arguments):
    response = client.get(f"/api/v1/cases/{cid}/stats")
    assert response.status_code == 200, response.text
    expected = stats_value(cid, **arguments)
    value = response.json()
    revision = value.pop("analysis_revision")
    assert len(revision) == 32
    assert value.pop("event_summary") is None
    assert value == expected
    legacy = client.get("/api/stats", params={"case_id": cid})
    assert legacy.status_code == 200, legacy.text
    assert legacy.json() == {**{key: expected[key] for key in (
        "flows", "accounts", "batches", "funds", "statistics_policy", "import_coverage")},
        "analysis_revision": revision, "event_summary": None}
    return expected


def assert_coverage_branches(database, cid, expected):
    # The supplied value is derived only from this current case while holding
    # the same lock. Independent expected coverage still checks every field.
    with database._lock:
        counts = database.q_raw(
            "select (select count(*) from flows where case_id=?) raw_count,"
            "(select count(*) from effective_flows where case_id=?) effective_count", [cid, cid])[0]
        excluded = counts["raw_count"] - counts["effective_count"]
        assert excluded == expected["excluded_flows"]
        assert import_coverage(cid) == expected
        assert import_coverage(cid, known_excluded=excluded) == expected


def done(client, cid, view):
    result = complete(client, cid, view)
    assert result["stage"] == "done", result
    return result


def shared_pair(client, cid, *, profile=V4, zero_witness=False, monkeypatch=None):
    rows = round_trip()
    if zero_witness:
        rows[0]["交易金额"] = "0.00"
    payload, archive = source(rows, path="bank/renamed.csv")
    ordinary = preview(client, cid, payload, "standalone.csv")
    done(client, cid, ordinary)
    proof = (frozen_preview(client, cid, archive, monkeypatch, profile)
             if profile == V3 else preview(client, cid, archive, "synthetic.zip"))
    done(client, cid, proof)
    return ordinary, proof, payload, archive


@pytest.mark.parametrize("batches", [0, 2], ids=["empty-case", "batches-without-flows"])
def test_stats_empty_effective_subset_still_reports_its_independent_batch_count(
        client, database, make_case, batches):
    cid = make_case("Synthetic empty stats case")
    for index in range(batches):
        database.x("insert into batches(case_id,filename) values (?,?)", [cid, f"synthetic-empty-{index}.csv"])
    expected = coverage_value()
    assert_stats(client, cid, observations=0, accounts=0, batches=batches, bounds=[], coverage=expected)
    assert_coverage_branches(database, cid, expected)


def test_all_raw_money_excluded_by_a_zero_witness_pair_is_an_empty_currency_result(
        client, database, make_case):
    cid = make_case("Synthetic all raw funds excluded stats")
    shared_pair(client, cid, zero_witness=True)
    assert database.q_raw("select count(*) n,sum(amount) amount from flows where case_id=?", [cid]) == [
        {"n": 1, "amount": Decimal("10.00")}]
    # Coverage keeps each non-monetary source coordinate; A02 discharges only
    # the ordinary negative diagnosis, without merging these two path records.
    expected = coverage_value(non_monetary=2, cancelled=2, excluded=1)
    assert_stats(client, cid, observations=0, accounts=0, batches=2, bounds=[], coverage=expected)
    assert_coverage_branches(database, cid, expected)


@pytest.mark.parametrize("rows,bounds", [
    ([("1.01", "USD"), ("2.02", "USD")], [("USD", "3.03")]),
    ([("1.01", ""), ("2.02", None)], [(None, None)]),
    ([("0.01", None), ("0.02", ""), ("1.01", "CNY"), ("2.02", "USD"), ("3.03", "CNY")],
     [("CNY", None), ("USD", None), (None, None)]),
    ([(None, "USD")], [("USD", None)]),
    ([('-1.00', "USD")], [("USD", None)]),
], ids=["single-currency", "empty-currency-group", "multiple-currencies", "legacy-null-amount-group",
        "legacy-negative-amount-group"])
def test_stats_keeps_each_currency_group_and_unknown_legacy_amount_explicit(
        client, database, make_case, rows, bounds):
    cid = make_case("Synthetic stats currency grouping")
    # Storage permits legacy NULL amounts and absent accounts. This is a
    # statistics contract fixture, not a new import acceptance rule.
    database.executemany("insert into flows(case_id,amount,currency) values (?,?,?)", [
        (cid, Decimal(value) if value is not None else None, code) for value, code in rows])
    expected = coverage_value()
    assert_stats(client, cid, observations=len(rows), accounts=0, batches=0, bounds=bounds, coverage=expected)
    assert_coverage_branches(database, cid, expected)


def test_stats_preserves_decimal_sum_and_exact_distinct_account_meaning(client, database, make_case):
    cid = make_case("Synthetic exact stats amount and account identities")
    database.x("insert into batches(case_id,filename) values (?,'synthetic-legacy.csv')", [cid])
    database.executemany(
        "insert into flows(case_id,payer_account,payee_account,amount,currency) values (?,?,?,?, 'CNY')", [
            (cid, None, None, Decimal("9999999999999999.99")),
            (cid, None, "", Decimal("0.01")),
            (cid, "SYN-A", "SYN-A", Decimal("0.02")),
            (cid, "SYN-A", "SYN-B", Decimal("1.23")),
            (cid, "", "SYN-B", Decimal("0.00")),
            (cid, " SYN-A ", "syn-a", Decimal("0.02")),
        ])
    expected = coverage_value()
    assert_stats(client, cid, observations=6, accounts=4, batches=1,
                 bounds=[("CNY", "10000000000000001.27")], coverage=expected)
    assert_coverage_branches(database, cid, expected)


def test_interleaved_case_stats_do_not_reuse_another_cases_subset_or_currency(client, database, make_case):
    first, second = make_case("Synthetic stats first scope"), make_case("Synthetic stats second scope")
    database.executemany("insert into flows(case_id,payer_account,payee_account,amount,currency) values (?,?,?,?,?)", [
        (first, "SYN-A", "SYN-B", Decimal("1.01"), "CNY"),
        (second, "SYN-A", "SYN-B", Decimal("999.99"), "USD"),
        (first, "SYN-B", "SYN-C", Decimal("2.02"), "CNY"),
    ])
    database.executemany("insert into batches(case_id,filename) values (?,?)", [
        (first, "first.csv"), (second, "second-a.csv"), (second, "second-b.csv")])
    for cid, flows, accounts, batches, amount, currency in [
        (first, 2, 3, 1, "3.03", "CNY"), (second, 1, 2, 2, "999.99", "USD"),
        (first, 2, 3, 1, "3.03", "CNY"),
    ]:
        assert_stats(client, cid, observations=flows, accounts=accounts, batches=batches,
                     bounds=[(currency, amount)], coverage=coverage_value())


@pytest.mark.parametrize("case_id", ["2147483647", "9999999999999999999"])
def test_nonexistent_case_retains_the_versioned_not_found_error(client, case_id):
    response = client.get(f"/api/v1/cases/{case_id}/stats")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "case_not_found", "message": "案件不存在，请重新选择案件"}}


def test_legacy_missing_case_stats_keeps_its_existing_empty_response(client):
    response = client.get("/api/stats", params={"case_id": 2147483647})
    assert response.status_code == 200, response.text
    assert response.json() == {"flows": 0, "accounts": 0, "batches": 0, "import_coverage": coverage_value()}


@pytest.mark.parametrize("profiles", [(V3,), (V4,), (V3, V4)], ids=["v3-only", "v4-only", "mixed-proofs"])
def test_default_and_known_excluded_coverage_preserve_versioned_proof_diagnostics(
        client, database, make_case, monkeypatch, profiles):
    cid = make_case("Synthetic stats versioned cancellation coverage")
    _ordinary, _proof, _payload, archive = shared_pair(client, cid, profile=profiles[0], monkeypatch=monkeypatch)
    if len(profiles) == 2:
        done(client, cid, preview(client, cid, archive, "current.zip"))
    current = V4 in profiles
    expected = coverage_value(unresolved=0 if current else 1, cancelled=2, excluded=1 if current else 0)
    amount = "1.00" if current else "11.00"
    assert_stats(client, cid, observations=1 if current else 2, accounts=2, batches=1 + len(profiles),
                 bounds=[("CNY", amount)], coverage=expected)
    assert_coverage_branches(database, cid, expected)


@pytest.mark.parametrize("kind", ["superseded-v5", "sticky-v6", "archive-v4"])
def test_known_excluded_count_does_not_change_old_and_new_history_marker_scopes(
        client, database, make_case, monkeypatch, kind):
    cid = make_case("Synthetic stats historical marker coverage")
    shared_pair(client, cid)
    if kind == "archive-v4":
        payload = ("date,payer_account,payee_account,amount,币种代码\n"
                   "2026-02-01,SYN-HDR-A,SYN-HDR-B,4.56,USD\n").encode()
        from test_table_container_identity import archive_bytes
        # A generic CSV opts into the archive family through a nested ZIP;
        # an ordinary single-layer ZIP retains the legacy feedback dispatcher.
        content = archive_bytes([("bank/currency.csv", payload)], nested=True)
        old = frozen_preview(client, cid, content, monkeypatch, V3)
        first = done(client, cid, old)
        later = preview(client, cid, content, "current-headers.zip")
        assert later["table_summary"]["profile"] == V4
        second = done(client, cid, later)
        assert (first["result"]["ok"], second["result"]["ok"]) == (1, 0)
        marker = table_import.ARCHIVE_HISTORY_CONFLICT_BASIS
        unresolved, total = 1, "5.56"
    else:
        payload = ("date,payer_account,payee_account,付款方账户名称,amount\n"
                   "2026-02-01,SYN-HDR-A,SYN-HDR-B,Synthetic payer,1.01\n").encode()
        old = profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v5")
        current = profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v6")
        early, later = (current, old) if kind == "superseded-v5" else (old, current)
        first, second = done(client, cid, early), done(client, cid, later)
        assert (first["result"]["ok"], second["result"]["ok"]) == (1, 0)
        marker = table_import.HISTORY_CONFLICT_BASIS
        unresolved, total = (0 if kind == "superseded-v5" else 1), "2.01"
    record = source_records(client, cid, later["import_id"])[0]
    assert (record["disposition"], record["reason_code"], record["flow_id"]) == ("unresolved", "conflicting_field", None)
    assert record["details"]["counterparty_basis"] == marker
    expected = coverage_value(unresolved=unresolved, cancelled=2, excluded=1)
    assert_stats(client, cid, observations=2, accounts=4, batches=4,
                 bounds=[("CNY", total)], coverage=expected)
    assert_coverage_branches(database, cid, expected)


@pytest.mark.parametrize("diagnosis", ["missing-time", "compound-negative", "role", "financial", "future-profile"])
def test_known_excluded_still_runs_all_unrelated_financial_role_and_rule_diagnostics(
        client, database, make_case, diagnosis):
    cid = make_case("Synthetic stats known count does not bypass diagnoses")
    shared_pair(client, cid)
    if diagnosis == "role":
        from test_table_source_role_parser import encoded as identity_bytes
        # The established source-role fixture makes two candidates for the
        # same card disagree, rather than relying on a mixed A/B layout.
        payload = identity_bytes(
            ["date", "本方账号", "本方卡号", "交易卡号", "direction", "amount"],
            [["2026-01-01", "SYN-OWN", "SYN-CARD-A", "SYN-CARD-B", "out", "1.23"]])
    elif diagnosis == "financial":
        from test_table_financial_mapping import table_bytes
        # A separate unit column is an explicit financial declaration; legacy
        # unit-bearing amount aliases alone do not promise this newer marker.
        payload = table_bytes(
            ["date", "payer_account", "payee_account", "amount", "金额单位"],
            [["2026-01-01", "SYN-BAD-A", "SYN-BAD-B", "1.01", "未定义单位"]])
    else:
        date = "2026-03-01" if diagnosis == "future-profile" else ("" if diagnosis == "missing-time" else "not-a-time")
        amount = "-2.00" if diagnosis == "compound-negative" else "2.00"
        payload = f"date,payer_account,payee_account,amount\n{date},SYN-BAD-A,SYN-BAD-B,{amount}\n".encode()
    view = preview(client, cid, payload, "diagnostic.csv")
    done(client, cid, view)
    record = source_records(client, cid, view["import_id"])[0]
    if diagnosis == "future-profile":
        assert record["disposition"] == "imported"
        database.x("update import_sources set parser_version='funds-trace-table-v99' where source_id=?", [view["import_id"]])
    else:
        assert record["disposition"] == "unresolved"
        if diagnosis == "role":
            assert record["reason_code"] == "conflicting_field"
            assert record["details"]["counterparty_basis"] == table_import.ROLE_CONFLICT_BASIS
        elif diagnosis == "financial":
            assert record["details"]["validation_basis"] == table_import.FINANCIAL_CONFLICT_BASIS
            assert "unknown_unit" in record["details"]["validation_issues"].split(",")
        elif diagnosis == "compound-negative":
            assert {"negative_amount", "invalid_time"} <= set(record["details"]["validation_issues"].split(","))
        else:
            assert record["reason_code"] == "missing_time"
    future = diagnosis == "future-profile"
    total = "3.00" if future else "1.00"
    expected = coverage_value(unresolved=1, cancelled=2, excluded=1)
    assert_stats(client, cid, observations=2 if future else 1, accounts=4 if future else 2, batches=3,
                 bounds=[("CNY", total)], coverage=expected)
    assert_coverage_branches(database, cid, expected)


def test_known_excluded_parameter_is_keyword_only(database, make_case):
    cid = make_case("Synthetic narrow coverage argument")
    with pytest.raises(TypeError):
        import_coverage(cid, 0)
    assert_coverage_branches(database, cid, coverage_value())


def test_public_stats_passes_only_the_current_case_count_under_the_same_database_lock(
        client, database, make_case, monkeypatch):
    paired, empty = make_case("Synthetic known excluded one"), make_case("Synthetic known excluded zero")
    shared_pair(client, paired)
    expected = {paired: coverage_value(cancelled=2, excluded=1), empty: coverage_value()}
    checked = []
    original = cases.import_coverage

    def locked_coverage(case_id, *, known_excluded=None):
        assert database._lock._is_owned()
        assert known_excluded == expected[case_id]["excluded_flows"]
        assert original(case_id) == expected[case_id]
        value = original(case_id, known_excluded=known_excluded)
        assert value == expected[case_id]
        checked.append((case_id, known_excluded))
        return value

    monkeypatch.setattr(cases, "import_coverage", locked_coverage)
    for cid in (paired, empty, paired):
        response = client.get(f"/api/v1/cases/{cid}/stats")
        assert response.status_code == 200, response.text
        assert response.json()["import_coverage"] == expected[cid]
    assert checked == [(paired, 1), (empty, 0), (paired, 1)]


def test_public_stats_classifies_wide_journal_notes_before_coverage_windows(
        client, database, make_case, monkeypatch):
    """The public result stays exact; WINDOW must retain classified columns only.

    Direct schema-valid journal history is intentional: this tests an existing
    stored case reader, not a new parser-admission rule. Existing tests above
    independently cover version priority, sticky conflicts and physical keys.
    """
    import json

    from test_import_coverage import assessment

    cid = make_case("Synthetic wide-note stats reader")
    other = make_case("Synthetic wide-note stats isolation")
    size, source_id, member = 512, "wide-note-source", "synthetic/wide.csv"
    database.x("insert into batches(case_id,filename) values (?,?)", [cid, "synthetic-wide.csv"])
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,
                   size_bytes,mode,mapping_json,sheet,parser_version,row_kind)
                   values (?,?,'synthetic-wide.csv',?,1,'B','{}','',
                           'funds-trace-table-v19','table_record')""",
               [source_id, cid, "a" * 64])
    database.x("""insert into import_members(case_id,source_id,member_path,role,sha256,
                   size_bytes,encoding,row_count) values (?,?,?,'table',?,1,'utf-8',?)""",
               [cid, source_id, member, "b" * 64, size + 2])
    flows = database.q_raw("""insert into flows(case_id,payer_account,payee_account,amount,currency)
                              select ?,'SYN-WIDE-A','SYN-WIDE-B',10.25,'CNY'
                              from range(?) returning id""", [cid, size])
    database.executemany("insert into flow_sources(case_id,source_id,source_row,flow_id,disposition) values (?,?,?,?,'imported')",
                         [(cid, source_id, row, item["id"]) for row, item in enumerate(flows, 1)])
    note = "SYNTHETIC-NOTE-" * 2048
    plain_details = json.dumps({"counterparty_basis": "", "validation_basis": "", "synthetic_note": note})
    assert len(plain_details.encode("utf-8")) > 30000
    conflict_details = json.dumps({"counterparty_basis": table_import.HISTORY_CONFLICT_BASIS,
                                   "validation_basis": "", "synthetic_note": note})
    records = [(cid, source_id, row, member, row, f"synthetic-wide-key-{row}",
                "imported", "financial", plain_details) for row in range(1, size + 1)]
    # This branch must inspect the JSON marker even though a different source
    # coordinate is non-monetary. The planner cannot discard all classification.
    records += [(cid, source_id, size + 1, member, size + 1, "synthetic-wide-conflict",
                 "unresolved", "conflicting_field", conflict_details),
                (cid, source_id, size + 2, member, size + 2, "synthetic-wide-zero",
                 "non_monetary", "zero_amount", plain_details)]
    database.executemany("""insert into import_records(case_id,source_id,record_no,member_path,
                             source_row,record_key,disposition,reason_code,details_json)
                             values (?,?,?,?,?,?,?,?,?)""", records)
    # Unknown rules remain a gap even when imported, but cannot leak into cid.
    assessment(database, other, "imported", version=99)
    database.x("""insert into flows(case_id,payer_account,payee_account,amount,currency)
                   values (?,'SYN-OTHER-A','SYN-OTHER-B',9.99,'USD')""", [other])

    captured = []
    original_query = database.q_raw

    def capture_coverage(sql, params=None):
        result = original_query(sql, params)
        # Identify the observable coverage aggregate, not a candidate CTE name.
        if all(field in sql for field in ("unresolved_records", "non_monetary_records",
                                          "cancelled_records", "excluded_flows")):
            captured.append((sql, params))
        return result

    monkeypatch.setattr(database, "q_raw", capture_coverage)
    for case_id, count, amount, currency, batches, expected_coverage in (
            (cid, size, "5248.00", "CNY", 1, coverage_value(unresolved=1, non_monetary=1)),
            (other, 1, "9.99", "USD", 0, coverage_value(unresolved=1))):
        response = client.get(f"/api/v1/cases/{case_id}/stats")
        assert response.status_code == 200, response.text
        value = response.json()
        assert len(value.pop("analysis_revision")) == 32
        assert value.pop("event_summary") is None
        assert value == stats_value(case_id, observations=count, accounts=2, batches=batches,
                                    bounds=[(currency, amount)], coverage=expected_coverage)
    assert len(captured) == 2
    sql, params = captured[0]
    plan = json.loads(database.con().execute(
        "EXPLAIN (ANALYZE, FORMAT JSON) " + sql, params).fetchone()[1])

    def operators(node):
        yield node
        for child in node.get("children", []):
            yield from operators(child)

    nodes = list(operators(plan))
    windows = [node for node in nodes if node.get("operator_type") == "WINDOW"]
    assert windows, "the physical-history priority and conflict windows must actually execute"
    assert max(node["operator_cardinality"] for node in windows) == size + 2
    assert any("details_json" in str(node.get("extra_info", {}).get("Projections", ""))
               for node in nodes if node.get("operator_type") == "TABLE_SCAN"), \
        "the wide source notes must still be read to classify the conflict marker"
    wide_windows = [node["extra_info"] for node in windows
                    if any(marker in json.dumps(node.get("extra_info", {})).lower()
                           for marker in ("details_json", "json_extract", "json_value"))]
    assert not wide_windows, (
        "coverage WINDOW retains or decodes complete journal notes instead of narrow classifications: "
        + json.dumps(wide_windows, ensure_ascii=False)[:1200])



def test_public_stats_does_not_hash_build_the_full_wide_journal(client, database, make_case, monkeypatch):
    """Committed all-imported history exposes a different build-side choice.

    Keep this distribution separate from the mixed-diagnosis/two-case window
    regression: those extra statistics can make the optimizer pick a small build.
    The money/coverage oracle is independent of either query plan.
    """
    import json

    cid = make_case("Synthetic committed wide-journal build")
    size = 512
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,
                   mode,mapping_json,sheet,parser_version,row_kind)
                   values ('synthetic-source',?,'synthetic.csv',?,1,'B','{}','',
                           'funds-trace-table-v19','table_record')""", [cid, "0" * 64])
    database.x("""insert into import_members(case_id,source_id,member_path,role,sha256,
                   size_bytes,encoding,row_count)
                   values (?,'synthetic-source','synthetic.csv','table',?,1,'utf-8',?)""",
               [cid, "0" * 64, size])
    database.x("""insert into flows(id,case_id,amount,currency,payer_account,payee_account)
                   select i+1,?,10.25,'CNY','SYN-A','SYN-B' from range(?) t(i)""", [cid, size])
    database.x("""insert into flow_sources(case_id,source_id,source_row,flow_id,disposition)
                   select ?,'synthetic-source',i+1,i+1,'imported' from range(?) t(i)""", [cid, size])
    details = json.dumps({"counterparty_basis": "", "validation_basis": "",
                          "synthetic_note": "SYNTHETIC-NOTE-" * 2048})
    assert len(details.encode("utf-8")) > 30000
    database.x("""insert into import_records(case_id,source_id,record_no,member_path,source_row,
                   record_key,disposition,reason_code,details_json)
                   select ?,'synthetic-source',i+1,'synthetic.csv',i+1,'synthetic-key-'||i,
                          'imported','financial',? from range(?) t(i)""", [cid, details, size])
    captured = []
    original_query = database.q_raw

    def capture_coverage(sql, params=None):
        result = original_query(sql, params)
        if all(field in sql for field in ("unresolved_records", "non_monetary_records",
                                          "cancelled_records", "excluded_flows")):
            captured.append((sql, params))
        return result

    monkeypatch.setattr(database, "q_raw", capture_coverage)
    response = client.get(f"/api/v1/cases/{cid}/stats")
    assert response.status_code == 200, response.text
    value = response.json()
    assert len(value.pop("analysis_revision")) == 32
    assert value.pop("event_summary") is None
    assert value == stats_value(cid, observations=size, accounts=2, batches=0,
                                bounds=[("CNY", "5248.00")], coverage=coverage_value())
    assert len(captured) == 1
    sql, params = captured[0]
    plan = json.loads(database.con().execute(
        "EXPLAIN (ANALYZE, FORMAT JSON) " + sql, params).fetchone()[1])

    def operators(node):
        yield node
        for child in node.get("children", []):
            yield from operators(child)

    def scans_raw_notes(node):
        # Trace actual build-child scan projections. A materialized producer is
        # a separate pipeline; do not mistake its JSON scan for payload in a
        # CTE_SCAN of already classified columns. No CTE name is assumed here.
        projections = node.get("extra_info", {}).get("Projections", [])
        return (node.get("operator_type") == "TABLE_SCAN" and "details_json" in projections
                or any(scans_raw_notes(child) for child in node.get("children", [])))

    nodes = list(operators(plan))
    windows = [node for node in nodes if node.get("operator_type") == "WINDOW"]
    assert windows and max(node["operator_cardinality"] for node in windows) == size
    assert any(node.get("operator_type") == "TABLE_SCAN" and scans_raw_notes(node)
               and node["operator_cardinality"] == size for node in nodes)
    wide_builds = []
    for node in nodes:
        if node.get("operator_type") != "HASH_JOIN":
            continue
        build = node["children"][1]
        # Small cancellation/proof sets may retain original notes. The normal
        # journal must not become the build payload merely to attach those sets.
        if build["operator_cardinality"] >= size and scans_raw_notes(build):
            wide_builds.append({"join": node.get("extra_info", {}),
                                "build_rows": build["operator_cardinality"],
                                "build": build.get("extra_info", {})})
    assert not wide_builds, (
        "coverage HASH_JOIN builds the full journal with original wide notes before classification: "
        + json.dumps(wide_builds, ensure_ascii=False))


def test_public_stats_keeps_money_and_resolution_in_one_locked_read(
        client, database, make_case, monkeypatch):
    """A real queued writer cannot splice its new revision into old totals."""
    import hashlib
    import json
    import threading

    from app import evidence_resolution

    cid = make_case("Synthetic atomic stats and resolution")
    flow_id = database.q_raw("""insert into flows(case_id,payer_account,payee_account,amount,currency)
                                values (?,'SYN-RACE-A','SYN-RACE-B',1.25,'CNY') returning id""", [cid])[0]["id"]
    snapshots = []
    for label in ("A", "B"):
        # Schema-valid immutable synthetic audits; their revisions use the
        # canonical protocol independently of the production hash helper.
        summary = {"policy": evidence_resolution.POLICY, "entities": 0, "resolved_endpoints": 0,
                   "unresolved_endpoints": 0, "conflicts": 0, "auxiliary_records": 0,
                   "evidence_records": 0, "note": "Synthetic committed resolution " + label}
        canonical = json.dumps([summary, [], []], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        revision = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        summary["revision"] = revision
        database.x("""insert into resolution_revisions(case_id,revision,summary_json,decisions_json)
                       values (?,?,?,?)""", [cid, revision, json.dumps(summary), '{"endpoints":[],"entities":[]}'])
        snapshots.append(summary)
    before, after = snapshots
    database.x("insert into resolution_state(case_id,revision) values (?,?)", [cid, before["revision"]])

    coverage_ready, writer_attempted, writer_done, stop = (threading.Event() for _ in range(4))
    writer_errors, lock_attempts = [], []
    real_coverage, real_summary = cases.import_coverage, evidence_resolution.summary

    def queued_writer():
        try:
            assert coverage_ready.wait(5), "stats never completed the coverage read"
            if stop.is_set():
                return
            acquired = database._lock.acquire(blocking=False)
            if acquired:
                database._lock.release()
            lock_attempts.append(acquired)
            writer_attempted.set()
            assert not acquired, "the stats reader must still own its lock after coverage"
            with database.transaction():
                if stop.is_set():
                    return
                database.x("update flows set amount=2.50 where case_id=? and id=?", [cid, flow_id])
                database.x("update resolution_state set revision=? where case_id=?", [after["revision"], cid])
                from app import analysis_snapshot
                analysis_snapshot.invalidate(cid)
        except BaseException as error:
            writer_errors.append(error)
        finally:
            writer_attempted.set()
            writer_done.set()

    def coverage_then_release_writer(case_id, *, known_excluded=None):
        value = real_coverage(case_id, known_excluded=known_excluded)
        assert database._lock._is_owned()
        if not coverage_ready.is_set():
            coverage_ready.set()
            assert writer_attempted.wait(5), "writer never attempted the held lock"
            assert not writer_errors, repr(writer_errors)
            assert lock_attempts == [False]
        return value

    def summary_after_a_possible_commit(case_id):
        # Only widen an already possible lock-free scheduling gap. A correctly
        # locked reader never waits for a writer that is blocked on this lock.
        if not database._lock._is_owned():
            assert writer_done.wait(5), "writer could not commit in the unlocked summary gap"
            assert not writer_errors, repr(writer_errors)
        return real_summary(case_id)

    monkeypatch.setattr(cases, "import_coverage", coverage_then_release_writer)
    monkeypatch.setattr(evidence_resolution, "summary", summary_after_a_possible_commit)
    writer = threading.Thread(target=queued_writer, name="synthetic-stats-writer", daemon=True)
    writer.start()
    try:
        first = client.get(f"/api/v1/cases/{cid}/stats")
        assert writer_done.wait(5), "writer remained blocked after the stats request returned"
        writer.join(timeout=5)
        assert not writer.is_alive()
        assert not writer_errors, repr(writer_errors)
        second = client.get(f"/api/v1/cases/{cid}/stats")
        assert first.status_code == second.status_code == 200, (first.text, second.text)

        def expected(amount, summary):
            return stats_value(cid, observations=1, accounts=2, batches=0,
                               bounds=[("CNY", amount)], coverage=coverage_value(), resolution=summary)

        values = [first.json(), second.json()]
        generations = [value.pop("analysis_revision") for value in values]
        assert all(len(revision) == 32 for revision in generations) and generations[0] != generations[1]
        assert all(value.pop("event_summary") is None for value in values)
        assert values == [expected("1.25", before), expected("2.50", after)]
    finally:
        stop.set()
        coverage_ready.set()
        writer.join(timeout=5)
        assert not writer.is_alive(), "synthetic writer must be joined before database fixture cleanup"
