"""Public synthetic imports keep subsecond chronology and source-order limits."""
from datetime import datetime
import io
import json
import re

import openpyxl
import pytest

from test_event_import import csv_rows
from test_folder_evidence import accepted
from test_principal_book_model import book_row, import_books


def timed_book(payer, payee, stamp, order, ref, own="B"):
    return {**book_row(own, payer, payee, "100.00", "100.00", 0, order, ref),
            "交易时间": "2026-01-01 10:00:00" + stamp}


def analysis(client, cid, surface, source, target):
    if surface == "paths":
        response = client.post("/api/analysis/paths", json={
            "case_id": cid, "src": source, "dst": target, "order": "time"})
    else:
        response = client.post("/api/analysis/" + surface, json={"case_id": cid, "params": {
            "algorithms": ["paths", "fast_flows"], "path_src": source, "path_dst": target,
            "path_order": "time", "ff_min_amount": "100.00", "ff_tolerance": 0}})
    assert response.status_code == 200, response.text
    if surface == "report":
        encoded = re.search(r'<script type="application/json" id="funds-trace-report-data">(.*?)</script>',
                            response.text, re.S)
        assert encoded is not None
        return json.loads(encoded.group(1))["results"], response.text
    return response.json(), ""


@pytest.mark.parametrize("reverse", [False, True])
def test_legacy_flows_and_tracked_export_preserve_fractional_source_times(client, make_case, reverse):
    cid = make_case("Synthetic precise legacy flow time")
    rows = [timed_book("A", "B", ".900001", 2, "IN"),
            timed_book("B", "C", ".100002", 1, "OUT")]
    imported = import_books(client, cid, rows[::-1] if reverse else rows)
    response = client.get("/api/flows", params={"case_id": cid})
    assert response.status_code == 200, response.text
    assert {row["trade_no"]: datetime.fromisoformat(row["trade_time"]).microsecond
            for row in response.json()["rows"]} == {"SYN-B-IN": 900001, "SYN-B-OUT": 100002}
    assert client.post("/api/track", json={"case_id": cid,
        "flow_ids": [row["id"] for row in imported.values()]}).status_code == 200
    exported = client.get("/api/export/tracked.xlsx", params={"case_id": cid})
    assert exported.status_code == 200, exported.text
    book = openpyxl.load_workbook(io.BytesIO(exported.content))
    records = list(book["原始账簿观察"].values)
    headers = records[0]
    assert {str(row[headers.index("交易时间")]) for row in records[1:]} == {
        "2026-01-01 10:00:00.900001", "2026-01-01 10:00:00.100002"}


@pytest.mark.parametrize("surface", ["paths", "run", "report"])
@pytest.mark.parametrize("reverse", [False, True])
def test_fractional_reverse_path_stays_topological_and_does_not_pair(client, make_case, surface, reverse):
    cid = make_case("Synthetic fractional reversed legs")
    rows = [timed_book("A", "B", ".900", 2, "IN"),
            timed_book("B", "C", ".100", 1, "OUT")]
    imported = import_books(client, cid, rows[::-1] if reverse else rows)
    result, text = analysis(client, cid, surface, imported["SYN-B-IN"]["payer_account"],
                            imported["SYN-B-OUT"]["payee_account"])
    paths = result if surface == "paths" else result["paths"]
    assert paths["count"] == 1
    path = paths["paths"][0]
    assert path["continuous"] is False
    assert path["temporal_status"] == "reversed"
    assert [datetime.fromisoformat(leg["time"]).microsecond for leg in path["path"]] == [900000, 100000]
    if surface != "paths":
        assert result["fast_flows"]["pairs"] == []
    if surface == "report":
        assert "时序不连续" in text and ".900000" in text and ".100000" in text


@pytest.mark.parametrize("orders,expected", [((1, 2), True), ((2, 1), False), (("", ""), None)])
@pytest.mark.parametrize("reverse", [False, True])
def test_same_time_path_uses_only_shared_source_posting_order(client, make_case, orders, expected, reverse):
    cid = make_case("Synthetic tied path source order")
    rows = [timed_book("A", "B", ".123456", orders[0], "IN"),
            timed_book("B", "C", ".123456", orders[1], "OUT")]
    imported = import_books(client, cid, rows[::-1] if reverse else rows)
    result, text = analysis(client, cid, "report", imported["SYN-B-IN"]["payer_account"],
                            imported["SYN-B-OUT"]["payee_account"])
    path = result["paths"]["paths"][0]
    assert path["continuous"] is expected
    assert path["temporal_status"] == ("unknown" if expected is None else "continuous" if expected else "reversed")
    assert len(result["fast_flows"]["pairs"]) == int(expected is True)
    if expected is None:
        assert "时序未确定" in text


def test_independent_source_sequences_do_not_order_equal_time_legs(client, make_case):
    cid = make_case("Synthetic unrelated posting scopes")
    rows = [timed_book("A", "B", ".123456", 1, "IN"),
            timed_book("B", "C", ".123456", 2, "OUT")]
    accepted(client, cid, [("synthetic-in.csv", csv_rows(rows[:1])),
                           ("synthetic-out.csv", csv_rows(rows[1:]))], scope_bank=None)
    imported = {row["trade_no"]: row for row in client.get(f"/api/v1/cases/{cid}/flows",
                params={"granularity": "observations"}).json()["items"]}
    result, _ = analysis(client, cid, "run", imported["SYN-B-IN"]["payer_account"],
                         imported["SYN-B-OUT"]["payee_account"])
    assert result["paths"]["paths"][0]["continuous"] is None
    assert result["fast_flows"]["pairs"] == []


def test_one_microsecond_forward_step_remains_continuous_and_pairable(client, make_case):
    cid = make_case("Synthetic microsecond forward step")
    imported = import_books(client, cid, [timed_book("A", "B", ".100001", 9, "IN"),
                                          timed_book("B", "C", ".100002", 1, "OUT")])
    result, _ = analysis(client, cid, "run", imported["SYN-B-IN"]["payer_account"],
                         imported["SYN-B-OUT"]["payee_account"])
    path = result["paths"]["paths"][0]
    assert path["continuous"] is True and path["temporal_status"] == "continuous"
    assert len(result["fast_flows"]["pairs"]) == 1


def test_missing_recorded_time_does_not_claim_path_continuity(client, database, make_case):
    cid = make_case("Synthetic unknown path time")
    imported = import_books(client, cid, [timed_book("A", "B", ".100001", 1, "IN"),
                                          timed_book("B", "C", ".100002", 2, "OUT")])
    database.x("update flows set trade_time=null where case_id=? and id=?", [cid, imported["SYN-B-IN"]["id"]])
    result, _ = analysis(client, cid, "run", imported["SYN-B-IN"]["payer_account"],
                         imported["SYN-B-OUT"]["payee_account"])
    path = result["paths"]["paths"][0]
    assert path["continuous"] is None and path["temporal_status"] == "unknown"
    assert result["fast_flows"]["pairs"] == []


def test_confirmed_events_use_the_shared_accounts_own_order_not_representative_ids(client, make_case):
    cid = make_case("Synthetic mirrored event time order")
    # The first event's representative is its A entry, but only B's source
    # sequence can establish that its incoming event precedes B's outgoing one.
    rows = [timed_book("A", "B", ".123456", 99, "IN", own="A"),
            timed_book("A", "B", ".123456", 1, "IN"),
            timed_book("B", "C", ".123456", 2, "OUT"),
            timed_book("B", "C", ".123456", 1, "OUT", own="C")]
    imported = import_books(client, cid, rows)
    result, _ = analysis(client, cid, "run", imported["SYN-A-IN"]["payer_account"],
                         imported["SYN-C-OUT"]["payee_account"])
    path = result["paths"]["paths"][0]
    assert str(path["path"][0]["id"]) == imported["SYN-A-IN"]["id"]
    assert path["continuous"] is True and path["temporal_status"] == "continuous"
    assert len(result["fast_flows"]["pairs"]) == 1


def test_unknown_equal_time_cycle_is_presented_as_an_unordered_clue(client, make_case):
    cid = make_case("Synthetic unordered cycle clue")
    import_books(client, cid, [timed_book("A", "B", ".123456", "", "IN"),
                               timed_book("B", "A", ".123456", "", "OUT")])
    response = client.post("/api/analysis/report", json={"case_id": cid, "params": {"algorithms": ["cycles"]}})
    assert response.status_code == 200, response.text
    encoded = re.search(r'<script type="application/json" id="funds-trace-report-data">(.*?)</script>', response.text, re.S)
    cycle = json.loads(encoded.group(1))["results"]["cycles"]["cycles"][0]
    assert cycle["continuous"] is None and cycle["temporal_status"] == "unknown"
    assert "时序未确定" in response.text


def test_cycle_does_not_ignore_the_start_accounts_contradictory_posting_order(client, make_case):
    cid = make_case("Synthetic contradictory same-time cycle")
    import_books(client, cid, [
        timed_book("A", "B", ".123456", 3, "AB", own="A"),
        timed_book("A", "B", ".123456", 1, "AB"),
        timed_book("B", "C", ".123456", 2, "BC"),
        timed_book("B", "C", ".123456", 1, "BC", own="C"),
        timed_book("C", "A", ".123456", 2, "CA", own="C"),
        timed_book("C", "A", ".123456", 1, "CA", own="A")])
    response = client.post("/api/analysis/run", json={"case_id": cid, "params": {"algorithms": ["cycles"]}})
    assert response.status_code == 200, response.text
    assert response.json()["cycles"]["cycles"] == []


def test_fractional_cycle_uses_the_actual_chronological_rotation(client, make_case):
    cid = make_case("Synthetic fractional cycle rotation")
    imported = import_books(client, cid, [timed_book("A", "B", ".900", 3, "AB"),
        timed_book("B", "C", ".100", 1, "BC"), timed_book("C", "A", ".500", 2, "CA", own="C")])
    response = client.post("/api/analysis/run", json={"case_id": cid, "params": {"algorithms": ["cycles"]}})
    assert response.status_code == 200, response.text
    cycle = response.json()["cycles"]["cycles"][0]
    assert cycle["accounts"] == [imported["SYN-B-BC"]["payer_account"],
                                  imported["SYN-B-BC"]["payee_account"], imported["SYN-B-AB"]["payer_account"]]
    assert [datetime.fromisoformat(edge["time"]).microsecond for edge in cycle["edges"]] == [100000, 500000, 900000]


def test_balance_chart_accepts_preserved_fractional_timestamps(monkeypatch):
    from app import report
    def rendered(fig):
        report.plt.close(fig)
        return "synthetic-chart"
    monkeypatch.setattr(report, "_fig64", rendered)
    assert report._balance_chart([{"self_account": "SYN-A", "currency": "CNY", "balance": "1.00",
        "trade_time": f"2026-01-01 10:00:00.{fraction}"} for fraction in ("100001", "200002", "900009")],
        "SYN-A", "Synthetic A") == "synthetic-chart"


@pytest.mark.parametrize("permutation", [(0, 2, 1), (0, 1, 2)])
def test_fast_flow_same_time_prefix_uses_proved_order_before_matching(client, make_case, permutation):
    cid = make_case("Synthetic proved same-time fast-flow prefix")
    rows = [book_row("B", "A", "B", "100.00", "150.00", 0, 1, "IN"),
            book_row("B", "B", "C", "50.00", "100.00", 0, 2, "FIRST"),
            book_row("B", "B", "D", "100.00", "0.00", 0, 3, "SECOND")]
    import_books(client, cid, [rows[index] for index in permutation])
    response = client.post("/api/analysis/run", json={"case_id": cid, "params": {
        "algorithms": ["fast_flows"], "ff_min_amount": "100.00", "ff_tolerance": 0}})
    assert response.status_code == 200, response.text
    # The chronological prefix is 50 then 100, not an arbitrary matching 100.
    assert response.json()["fast_flows"]["pairs"] == []


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("amounts,paired", [(("50.00", "100.00"), False), (("40.00", "60.00"), True)])
def test_fast_flow_unknown_outgoing_tie_is_whole_group_not_id_prefix(client, make_case, reverse, amounts, paired):
    cid = make_case("Synthetic unknown outgoing prefix")
    rows = [book_row("B", "A", "B", "100.00", "150.00", 0, "", "IN"),
            book_row("B", "B", "C", amounts[0], "", 1, "", "ONE"),
            book_row("B", "B", "D", amounts[1], "", 1, "", "TWO")]
    imported = import_books(client, cid, rows[::-1] if reverse else rows)
    response = client.post("/api/analysis/run", json={"case_id": cid, "params": {
        "algorithms": ["fast_flows"], "ff_min_amount": "100.00", "ff_tolerance": 0}})
    assert response.status_code == 200, response.text
    pairs = response.json()["fast_flows"]["pairs"]
    assert len(pairs) == int(paired)
    if paired:
        assert {str(row["id"]) for row in pairs[0]["out_flows"]} == {
            imported["SYN-B-ONE"]["id"], imported["SYN-B-TWO"]["id"]}


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("known", [False, True])
def test_fast_flow_competing_incoming_tie_uses_source_order_or_stays_unknown(client, make_case, reverse, known):
    cid = make_case("Synthetic competing incoming prefix")
    rows = [book_row("B", "A", "B", "100.00", "100.00", 0, 1 if known else "", "FIRST"),
            book_row("B", "D", "B", "100.00", "200.00", 0, 2 if known else "", "SECOND"),
            book_row("B", "B", "C", "100.00", "100.00", 1, "", "OUT")]
    imported = import_books(client, cid, rows[::-1] if reverse else rows)
    response = client.post("/api/analysis/run", json={"case_id": cid, "params": {
        "algorithms": ["fast_flows"], "ff_min_amount": "100.00", "ff_tolerance": 0}})
    assert response.status_code == 200, response.text
    pairs = response.json()["fast_flows"]["pairs"]
    assert len(pairs) == int(known)
    if known:
        assert str(pairs[0]["in_flow"]["id"]) == imported["SYN-B-FIRST"]["id"]
