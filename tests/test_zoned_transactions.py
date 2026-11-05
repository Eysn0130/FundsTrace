"""Independent synthetic public behavior for explicit offsets and mixed clocks."""
import csv
import io
import json

import pytest

from app import db, table_import
from test_folder_evidence import complete

TIMES = ("2026-04-06T01:02:03.456789+09:00", "2026-04-05T16:02:03.456789Z",
         "2026-04-05T12:30:00.000007-04:00")


def payload(mixed=False):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["交易时间", "付款账号", "收款账号", "交易金额", "币种", "交易流水号"])
    values = [*zip(TIMES, ("2.01", "3.02", "4.03"))]
    if mixed:
        values += [("2026-04-06 01:02:03.456789", "5.04"), ("2026-04-06T01:02:03+27:00", "6.05")]
    for index, (stamp, amount) in enumerate(values):
        writer.writerow([stamp, "SYN-ZONE-A", "SYN-ZONE-B", amount, "CNY", f"SYN-ZONE-{index}"])
    return output.getvalue().encode("utf-8-sig")


def imported(client, make_case, mixed=False):
    cid = make_case("Synthetic zoned transaction")
    view = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("zone.csv", payload(mixed))}).json()
    assert view["stats"]["ok"] == (4 if mixed else 3)
    assert complete(client, cid, view)["stage"] == "done"
    return cid, view


def test_zoned_flows_graph_source_export_and_reopen(client, make_case):
    cid, view = imported(client, make_case)
    def check(case):
        page = client.get(f"/api/v1/cases/{case}/flows").json()
        assert page["total"] == 3
        rows = sorted(page["items"], key=lambda row: row["trade_no"])
        assert [row["trade_time"] for row in rows] == list(TIMES)
        assert [row["trade_time_utc"] for row in rows] == ["2026-04-05T16:02:03.456789Z"] * 2 + ["2026-04-05T16:30:00.000007Z"]
        assert {row["trade_time_basis"] for row in rows} == {"utc"}
        assert client.get(f"/api/v1/cases/{case}/stats").json()["amount_total"] == "9.06"
        return rows
    rows = check(cid)
    client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"] for row in rows]})
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert len(graph["edges"]) == 1 and graph["edges"][0]["cnt"] == 3
    assert graph["edges"][0]["total"] == "9.06"
    packed = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("zone.fundcase", packed.content)})
    assert restored.status_code == 200, restored.text
    check(restored.json()["case_id"])
    with db._lock:
        db._con.close(); db._con = None
    check(cid)
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    evidence = [json.loads(row["details"]["source_time_evidence"])["values"][0] for row in records if row["disposition"] == "imported"]
    assert [row["raw"] for row in evidence] == list(TIMES)


def test_mixed_wall_date_filter_and_absolute_window(client, make_case):
    cid, _ = imported(client, make_case, True)
    page = client.get(f"/api/v1/cases/{cid}/flows").json()
    assert page["total"] == 4
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["amount_total"] == "14.10"
    bare = [row for row in page["items"] if row["amount"] == "5.04"][0]
    assert bare["trade_time_basis"] == "unqualified" and bare["trade_time_utc"] is None
    wall = client.get("/api/flows", params={"case_id": cid, "tfrom": "2026-04-06", "tto": "2026-04-06"}).json()
    assert {row["amount"] for row in wall["rows"]} == {"2.01", "5.04"}
    absolute = client.get("/api/flows", params={"case_id": cid, "tfrom": "2026-04-05T16:02:03.456789Z", "tto": "2026-04-05T16:02:03.456789Z"}).json()
    assert {row["amount"] for row in absolute["rows"]} == {"2.01", "3.02"}
    client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"] for row in page["items"]]})
    result = client.post("/api/canvas/investigation/time", json={"case_id": cid}).json()
    assert result["summary"]["time_basis"] == "mixed"
    assert result["summary"]["time_ordering"] == "unknown" and result["timeline"] == []
    assert result["summary"]["amount_totals"][0]["amount"] == "14.10"
    absolute_time = client.post("/api/canvas/investigation/time", json={"case_id": cid,
        "start": "2026-04-05T16:02:03.456789Z", "end": "2026-04-05T16:02:03.456789Z"}).json()
    assert absolute_time["summary"]["selected_count"] == 2
    assert absolute_time["summary"]["time_basis"] == "utc"
    assert absolute_time["summary"]["time_incomparable_count"] == 1
    assert len(absolute_time["timeline"]) == 1
    assert absolute_time["summary"]["transactions"] == {"cnt": 2, "total": "5.03", "amount_totals": [{"currency": "CNY", "amount": "5.03"}]}
    assert result["summary"]["transactions"]["cnt"] == 4
    assert result["summary"]["transactions"]["total"] == "14.10"



def test_old_time_profile_keeps_rejection():
    assert len(table_import.FLOW_FIELDS) == 13
    assert {"trade_time_utc", "trade_time_original", "trade_time_offset"}.isdisjoint(table_import.FLOW_FIELDS)
    plan = table_import.parse(io.BytesIO(payload()), suffix=".csv", profile=table_import.TIME_ROLE_PROFILE)
    assert not plan["rows"]
    assert {row["reason_code"] for row in plan["records"] if row["source_row"] > 1} == {"unsupported_timezone"}


@pytest.mark.parametrize("field", ["trade_time_utc", "trade_time_original", "trade_time_offset"])
@pytest.mark.parametrize("profile", [table_import.TIME_ROLE_PROFILE, table_import.ZONED_PROFILE])
def test_derived_time_metadata_cannot_be_manually_mapped(client, make_case, monkeypatch, field, profile):
    from app.services import imports
    cid = make_case("Synthetic derived time mapping")
    with monkeypatch.context() as pinned:
        pinned.setattr(imports, "TABLE_PROFILE", profile)
        view = client.post(f"/api/v1/cases/{cid}/imports/preview",
            files={"file": ("clock.csv", payload())}).json()
    assert view["stats"]["ok"] == (3 if profile == table_import.ZONED_PROFILE else 0)
    receipt = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json()
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview",
        json={"mode": view["mode"], "mapping": {**view["mapping"], field: "交易时间"}})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_mapping"
    assert client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json() == receipt


@pytest.mark.parametrize("second,accepted", [
    ("2026-03-03T16:05:01.123456Z", 1), ("2026-03-04T00:05:01.123456Z", 0)])
def test_multiple_explicit_transaction_columns_compare_absolute_instants(client, make_case, second, accepted):
    first = "2026-03-04T00:05:01.123456+08:00"
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["交易时间", "transaction time", "付款账号", "收款账号", "交易金额", "币种"])
    writer.writerow([first, second, "SYN-MULTI-A", "SYN-MULTI-B", "2.73", "CNY"])
    cid = make_case("Synthetic two explicit time columns")
    view = client.post(f"/api/v1/cases/{cid}/imports/preview",
        files={"file": ("clocks.csv", output.getvalue().encode("utf-8-sig"))}).json()
    assert view["stats"]["ok"] == accepted
    assert complete(client, cid, view)["stage"] == "done"
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    assert len(records) == 1
    values = json.loads(records[0]["details"]["source_time_evidence"])["values"]
    assert [value["raw"] for value in values] == [first, second]
    assert all(value["role"] == "transaction" for value in values)
    if accepted:
        stats = client.get(f"/api/v1/cases/{cid}/stats").json()
        assert stats["flows"] == 1 and stats["amount_total"] == "2.73"
        assert len({value["utc"] for value in values}) == 1
    else:
        assert records[0]["reason_code"] == "conflicting_time"
        assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0


@pytest.mark.parametrize("header,value", [("date", "2026-01-01 12:00:00"), ("time of day", "12:00:00")])
def test_old_midnight_interpretation_replays_while_new_profile_rejects_conflict(client, make_case, monkeypatch, header, value):
    from app.services import imports
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["交易时间", header, "付款账号", "收款账号", "交易金额", "币种"])
    writer.writerow(["2026-01-01 00:00:00", value, "SYN-OLD-A", "SYN-OLD-B", "10.00", "CNY"])
    data = output.getvalue().encode("utf-8-sig")
    old_case = make_case("Synthetic pinned midnight")
    with monkeypatch.context() as pinned:
        pinned.setattr(imports, "TABLE_PROFILE", table_import.TIME_ROLE_PROFILE)
        old = client.post(f"/api/v1/cases/{old_case}/imports/preview",
            files={"file": ("midnight.csv", data)}).json()
    assert old["stats"]["ok"] == 1
    new_case = make_case("Synthetic new midnight conflict")
    current = client.post(f"/api/v1/cases/{new_case}/imports/preview",
        files={"file": ("midnight.csv", data)}).json()
    assert current["stats"]["ok"] == 0
    assert complete(client, new_case, current)["stage"] == "done"
    records = client.get(f"/api/v1/cases/{new_case}/imports/{current['import_id']}/records").json()["items"]
    assert records[0]["reason_code"] == "conflicting_time"
    refreshed = client.post(f"/api/v1/cases/{old_case}/imports/{old['import_id']}/preview",
        json={"mode": old["mode"], "mapping": old["mapping"]}).json()
    assert refreshed["stats"] == old["stats"]
    assert refreshed["table_summary"] == old["table_summary"]
    assert refreshed["table_summary"]["profile"] == table_import.TIME_ROLE_PROFILE
    assert complete(client, old_case, refreshed)["stage"] == "done"
    before = client.get(f"/api/v1/cases/{old_case}/flows").json()["items"]
    assert len(before) == 1 and before[0]["trade_time"].replace("T", " ") == "2026-01-01 00:00:00"
    packed = client.get(f"/api/cases/{old_case}/export")
    assert packed.status_code == 200
    restored = client.post("/api/cases/import", files={"file": ("pinned.fundcase", packed.content)})
    assert restored.status_code == 200, restored.text
    stats = client.get(f"/api/v1/cases/{restored.json()['case_id']}/stats").json()
    assert stats["flows"] == 1 and stats["amount_total"] == "10.00"


@pytest.mark.parametrize("other,confirmed", [(TIMES[1], 1), ("2026-04-06T01:02:03.456789Z", 0),
                                            ("2026-04-06 01:02:03.456789", 0)])
def test_strong_event_reference_compares_instants_and_keeps_balances(client, make_case, monkeypatch, other, confirmed):
    from app import event_resolution
    from test_event_import import transaction, csv_rows
    from test_folder_evidence import accepted
    cid = make_case("Synthetic zoned event reference")
    files = [("a.csv", csv_rows([transaction("SYN-A", **{"交易时间": TIMES[0]})])),
             ("b.csv", csv_rows([transaction("SYN-B", **{"交易时间": other})]))]
    _, receipt = accepted(client, cid, files, scope_bank=None)
    assert receipt["ok"] == 2
    assert event_resolution.summary(cid)["confirmed_events"] == confirmed
    observations = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()
    assert observations["total"] == 2
    assert {row["balance"] for row in observations["items"]} == {"50.00", "200.00"}
    if confirmed:
        assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 1
        assert client.get(f"/api/v1/cases/{cid}/stats").json()["amount_total"] == "100.00"
        client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"] for row in observations["items"]]})
        window = client.post("/api/canvas/investigation/time", json={"case_id": cid}).json()
        assert window["summary"]["transactions"]["cnt"] == 1
        assert window["summary"]["transactions"]["total"] == "100.00"
    else:
        members = event_resolution.members(cid)
        stats = client.get(f"/api/v1/cases/{cid}/stats").json()
        if other.endswith("Z"):
            assert {item["status"] for item in members} == {"conflict"}
            assert {item["reason"] for item in members} == {"conflicting_event_facts"}
        else:
            assert {item["status"] for item in members} == {"unknown"}
            assert {item["reason"] for item in members} == {"time_basis_incomparable"}
            assert stats["amount_total"] is None
            assert stats["flows"] is None and stats["funds"]["observation_count"] == 2
            legacy_stats = client.get("/api/stats", params={"case_id": cid}).json()
            assert legacy_stats["flows"] is None and legacy_stats["funds"]["observation_count"] == 2
            assert stats["event_summary"]["conflict_entries"] == 0
            for row in observations["items"]:
                source = client.get(f"/api/v1/cases/{cid}/flows/{row['id']}/sources").json()["items"][0]
                assert source["record_details"]["current_event_reason"] == "time_basis_incomparable"
            client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"] for row in observations["items"]]})
            graph = client.get("/api/graph", params={"case_id": cid}).json()
            assert len(graph["edges"]) == 1
            assert graph["edges"][0]["cnt"] is None and graph["edges"][0]["total"] is None
            assert graph["edges"][0]["time_basis_pending"] is True
            assert len(set(graph["edges"][0]["ids"])) == 2
            from urllib.parse import quote
            account = observations["items"][0]["payer_account"]
            for endpoint in (f"/api/accounts/{quote(account, safe='')}/profile", "/api/account-registry/profile"):
                result = client.get(endpoint, params={"case_id": cid, "account": account})
                assert result.status_code == 200, result.text
            from app import funds_projection
            selected_source = "select * from effective_flows where case_id=?"
            page, _ = funds_projection.page_with_summary(selected_source, [cid], 50, 0,
                observations=True, unique_source=True)
            assert len(page) == 2 and all(row["fact_time_pending"] is True for row in page)
            assert len({row["fact_pending_reference"] for row in page}) == 1
            assert page[0]["fact_pending_reference"] is not None
            single, _ = funds_projection.page_with_summary(selected_source + " and id=?",
                [cid, page[0]["id"]], 50, 0, observations=True, unique_source=True)
            assert len(single) == 1 and single[0]["fact_time_pending"] is False
            with monkeypatch.context() as chunks:
                chunks.setattr(funds_projection, "CHUNK", 1)
                full = client.post("/api/canvas/investigation/time", json={"case_id": cid}).json()
            assert full["summary"]["transactions"]["total"] is None
            assert full["summary"]["transactions"]["cnt"] is None
            one = client.post("/api/canvas/investigation/time", json={"case_id": cid,
                "start": "2026-04-05T16:02:03.456789Z", "end": "2026-04-05T16:02:03.456789Z"}).json()
            assert one["summary"]["transactions"]["cnt"] == 1
            assert one["summary"]["transactions"]["total"] == "100.00"
            known_case, _ = imported(client, make_case)
            known_rows = client.get(f"/api/v1/cases/{known_case}/flows").json()["items"]
            client.post("/api/track", json={"case_id": known_case, "flow_ids": [row["id"] for row in known_rows]})
            known_graph = client.get("/api/graph", params={"case_id": known_case}).json()
            import pathlib, subprocess
            root = pathlib.Path(__file__).resolve().parents[1]
            script = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=process.argv[1],data=JSON.parse(fs.readFileSync(0,'utf8'));
const source=fs.readFileSync(root+'/static/app.js','utf8');
const start=source.indexOf('function edgeLabel(e) {'),end=source.indexOf('\nfunction renderGraph(',start);
const constants=source.slice(source.indexOf('const fmtMoney ='),source.indexOf('function moneyHtml('));
const context=vm.createContext({state:{edgeLabels:new Set(['money'])}});
vm.runInContext(fs.readFileSync(root+'/static/money.js','utf8'),context);
vm.runInContext(constants+'\n'+source.slice(start,end),context);
assert.equal(context.edgeLabel(data.uncertain),'');
assert.equal(context.edgeLabel(data.known),'3笔·¥9.06');
"""
            subprocess.run(["node", "-e", script, str(root)], input=json.dumps({"uncertain": graph["edges"][0],
                "known": known_graph["edges"][0]}), text=True, check=True, capture_output=True)

    _, repeated = accepted(client, cid, files[::-1], scope_bank=None, zipped=True)
    assert (repeated["ok"], repeated["rows_dup"]) == (0, 2)
    assert event_resolution.summary(cid)["confirmed_events"] == confirmed
    packed = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("event.fundcase", packed.content)})
    assert restored.status_code == 200, restored.text
    assert event_resolution.summary(restored.json()["case_id"])["confirmed_events"] == confirmed


def test_context_uses_utc_ties_and_does_not_order_mixed_ledger(client, make_case):
    from test_principal_book_model import book_row, import_books
    cid = make_case("Synthetic mixed context clock")
    rows = [book_row("B", "EXT", "B", "20.00", "20.00", 0, "", "IN"),
            book_row("B", "B", "C", "5.00", "15.00", 1, "", "TIE"),
            book_row("B", "B", "D", "5.00", "10.00", 2, "", "LATER")]
    for row, stamp in zip(rows, TIMES):
        row["交易时间"] = stamp
    flows = import_books(client, cid, rows)
    anchor = flows["SYN-B-IN"]
    context = client.get(f"/api/context/{anchor['id']}", params={"case_id": cid, "x": 4}).json()
    assert {row["trade_no"]: row["context_relation"] for row in context["rows"]} == {
        "SYN-B-IN": "anchor", "SYN-B-TIE": "same_time", "SYN-B-LATER": "after"}
    assert "posting_order_unknown" in context["ordering"]["reasons"]
    bare = book_row("B", "B", "E", "1.00", "9.00", 3, "", "BARE")
    bare["交易时间"] = "2026-04-06 01:02:03.456789"
    import_books(client, cid, [bare])
    context = client.get(f"/api/context/{anchor['id']}", params={"case_id": cid, "x": 4}).json()
    assert context["ordering"] == {"status": "unknown", "reasons": ["mixed_time_basis"]}
    assert {row["context_relation"] for row in context["rows"]} <= {"anchor", "unordered"}
    from app import analytics
    result = analytics.match_fast_flows(cid, min_amount=0)
    assert result["time_ordering"] == "unknown"
    assert {group["time_basis"] for group in result["by_currency"]} == {"utc", "unqualified"}


def test_ready_old_profile_and_receipt_do_not_reinterpret_after_upgrade(client, make_case, monkeypatch):
    from app.services import imports
    cid = make_case("Synthetic old zoned ready session")
    with monkeypatch.context() as context:
        context.setattr(imports, "TABLE_PROFILE", table_import.TIME_ROLE_PROFILE)
        old = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("old.csv", payload())}).json()
    assert old["stats"]["ok"] == 0
    refreshed = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview",
                            json={"mode": old["mode"], "mapping": old["mapping"]}).json()
    assert refreshed["table_summary"]["profile"] == table_import.TIME_ROLE_PROFILE
    assert refreshed["stats"]["ok"] == 0
    assert complete(client, cid, refreshed)["stage"] == "done"
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0


def test_last_supported_calendar_date_is_a_valid_public_filter(client, make_case):
    cid = make_case("Synthetic maximum date boundary")
    response = client.get("/api/flows", params={"case_id": cid, "tto": "9999-12-31"})
    assert response.status_code == 200
    assert response.json()["total"] == 0
