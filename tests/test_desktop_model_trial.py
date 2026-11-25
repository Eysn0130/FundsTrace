"""Synthetic original revalidation and local model transport for the desktop trial."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from threading import Thread
from time import monotonic, sleep

import pytest
from openpyxl import Workbook

from app.services import desktop_model_trial as trial, imports
from test_api_v1 import poll
from test_assistance import ask_flow
from test_bank_posting_pairs import encoded, entries
from test_import_layout_review import UNKNOWN, setup_review
from test_principal_book_model import book_row, import_books
from test_source_ledger import add_source
from test_trace_review import review


ORIGINS = {"d1-2b": "http://127.0.0.1:49001", "d3-nano": "http://127.0.0.1:49002", "qwen35-4b": "http://[::1]:49003"}
TRIAL_CSV = ("字段口径：本端账号是逐笔交易的本方账号；本方备查账户只用于历史档案查阅，不参加这些交易。\n"
             "本端账号,本方备查账户,证件号\n"
             "SYN-OWNER-PRIVATE,SYN-LEAD-PRIVATE,SYN-ID-PRIVATE\n"
             "SYN-OWNER-PRIVATE,SYN-OTHER-PRIVATE,SYN-ID-PRIVATE\n").encode()


def test_model_configuration_accepts_only_explicit_loopback_origins():
    assert trial.parse_model_endpoints(None) is None
    assert trial.parse_model_endpoints(json.dumps(ORIGINS)) == {
        "d1-2b": ("127.0.0.1", 49001), "d3-nano": ("127.0.0.1", 49002), "qwen35-4b": ("::1", 49003)}
    for origin in ("http://127.0.0.1", "http://127.0.0.1:0", "http://127.0.0.1:65536", "http://localhost:49100",
                   "https://127.0.0.1:49100", "http://127.1:49100", "http://127.0.0.1:49100/path",
                   "http://127.0.0.1:49100#fragment", "http://127.0.0.1:49100?a=1",
                   "http://127.0.0.1:49100@example.invalid", "http://[::ffff:127.0.0.1]:49100",
                   ORIGINS["d3-nano"]):
        with pytest.raises(ValueError):
            trial.parse_model_endpoints({**ORIGINS, "d1-2b": origin})
    with pytest.raises(ValueError):
        trial.parse_model_endpoints({"d1-2b": ORIGINS["d1-2b"]})


def test_isolated_lite_comparison_preserves_its_explicit_role():
    lite = {tag: origin for tag, origin in ORIGINS.items() if tag != "d3-nano"}
    lite["d3-lite"] = ORIGINS["d3-nano"]
    assert trial.first_reviewers(trial.parse_model_endpoints(lite)) == ("d1-2b", "d3-lite")
    assert trial.first_reviewers(trial.parse_model_endpoints(ORIGINS)) == ("d1-2b", "d3-nano")
    with pytest.raises(ValueError):
        trial.parse_model_endpoints({**ORIGINS, "d3-lite": "http://127.0.0.1:49104"})


class ModelStub:
    def __init__(self, first, recheck=None):
        self.decide = first
        self.reconsider = recheck
        self.calls = []

    def first(self, tag, view):
        self.calls.append((tag, view))
        return self.decide(tag, view)

    def recheck(self, view, previous, conflicts):
        self.calls.append(("qwen35-4b", view))
        assert self.reconsider is not None, "unexpected recheck"
        return self.reconsider(view, previous, conflicts)

    columns = first
    reconsider_columns = recheck


def configure(client, monkeypatch, stub=None):
    endpoints = trial.parse_model_endpoints(ORIGINS) if stub else None
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (endpoints,), raising=False)
    if stub:
        monkeypatch.setattr(trial, "_LoopbackProvider", lambda _: stub)


def model_choice(ref, finding, *, cite=True):
    return {"answer": ref, "evidence": [ref] if cite else [], "finding": finding}


def column_choices(view, findings=("supported", "refuted"), *, cite=True):
    note = view["notes"][0]
    return {column: {"finding": finding, "evidence": [column, note] if cite and finding != "unknown" else []}
            for column, finding in zip(view["candidates"], findings, strict=True)}


def import_trial(client, case, raw=TRIAL_CSV, header=2):
    view, route, revision = setup_review(client, case, raw)
    body = {**revision, "member_path": "table.csv", "header_row": header, "field": "self_account"}
    return view, route + "/model-trial", body


def pair_trial(client, case, *, confirmed=False, day_only=False):
    values = entries()
    if not confirmed:
        values[-1].update({"交易时间": "2038-02-04 09:00:04", "交易流水号": "SYN-FIRST-LATE"})
        values.append({**values[-1], "交易时间": "2038-02-04" if day_only else "2038-02-04 09:00:05",
                       "交易流水号": "SYN-SECOND-LATE"})
        if day_only:
            prefix = "SYN-" + "X" * 116
            values[1]["交易流水号"] = prefix + "A"
            values[-1]["交易流水号"] = prefix + "B"
    add_source(client, case, encoded(values))
    items = client.get(f"/api/v1/cases/{case}/flows", params={"granularity": "observations"}).json()["items"]
    anchor = next(row for row in items if row["raw_self_account"] == "SYN-A" and row["amount"] == "37.41")
    view = review(client, case, anchor["id"], x=10)
    route, body, packet = ask_flow(client, case, anchor["id"], view, "pair")
    return route.replace("review-assistance", "review-model-trial"), body, packet


@pytest.mark.parametrize("raw", [
    "字段甲,字段乙\nSYN-1,SYN-1\nSYN-2,SYN-2\n",
    "甲账户,乙账户\nSYN-1,SYN-1\nSYN-2,SYN-2\n",
    "调单账户,证件号\nSYN-X,SYN-1\nSYN-Y,SYN-2\n",
    "本方备查账户1,本方备查账户2\nSYN-1,SYN-1\nSYN-2,SYN-2\n",
])
def test_no_models_when_import_has_fewer_than_two_distinguishable_candidates(client, make_case, monkeypatch, raw):
    case = make_case()
    _, route, body = import_trial(client, case, raw.encode(), header=2 if raw.startswith("口径说明") else 1)
    stub = ModelStub(lambda *_: pytest.fail("candidate gate must skip the models"))
    configure(client, monkeypatch, stub)
    response = client.post(route, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["state"], result["reason"], result["first_calls"], result["recheck_calls"]) == (
        "pending", "insufficient_evidence", 0, 0)
    assert result["packet"]["selection"] is None and not stub.calls


@pytest.mark.parametrize("original, choice", [
    ("字段口径：发生时点表示交易时间，已调单账户表示本方账号\n".encode() + UNKNOWN, "C2"),
    (("口径说明：证件号是代理人证件号；乙账户是交易本方账号。\n"
      "证件号,乙账户\nSYN-1,SYN-1\nSYN-2,SYN-2\n").encode(), "C2"),
])
def test_unique_original_note_is_a_pending_browse_position_without_model_calls(client, make_case, monkeypatch, original, choice):
    _, route, body = import_trial(client, make_case(), original)
    stub = ModelStub(lambda *_: pytest.fail("a sole note-backed lead skips model calls"))
    configure(client, monkeypatch, stub)
    response = client.post(route, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["state"], result["reason"], result["first_calls"], result["recheck_calls"]) == (
        "pending", "single_original_lead", 0, 0)
    selected = result["packet"]["selection"]
    assert selected["status"] == "review_only" and selected["choice"] == choice
    assert selected["refs"][0] == choice and len(selected["refs"]) == 2
    note = next(row for row in result["packet"]["records"] if row["id"] == selected["refs"][1])
    assert note["locator"]["source_row"] == 1 and "本方账号" in note["raw"]["cells"][0]
    assert not stub.calls


def test_trial_requires_config_and_agreement_remains_a_read_only_original_position(client, database, make_case, monkeypatch):
    case = make_case()
    view, route, body = import_trial(client, case)
    before = database.q_raw("select * from import_sessions where import_id=?", [view["import_id"]])
    with monkeypatch.context() as patch:
        configure(client, patch)
        response = client.post(route, json=body)
        assert response.status_code == 200, response.text
        assert (response.json()["state"], response.json()["reason"]) == ("unavailable", "not_configured")
    stub = ModelStub(lambda _, packet: column_choices(packet))
    configure(client, monkeypatch, stub)
    response = client.post(route, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["state"], result["reason"], result["first_calls"], result["recheck_calls"]) == (
        "proposal", "first_agreement", 2, 0)
    assert result["packet"]["selection"] == {
        "status": "review_only", "choice": "C1", "refs": ["C1", "N3"], "gap": None}
    assert result["column_checks"] == [
        {"column_ref": "C1", "finding": "supported", "note_ref": "N3"},
        {"column_ref": "C2", "finding": "refuted", "note_ref": "N3"},
    ]
    assert [tag for tag, _ in stub.calls] == ["d1-2b", "d3-nano"]
    material = json.dumps(stub.calls[0][1], ensure_ascii=False)
    for private in (body["revision"], view["import_id"], result["packet"]["packet_sha256"],
                    "SYN-OWNER-PRIVATE", "SYN-LEAD-PRIVATE", "SYN-ID-PRIVATE", "table.csv", "证件号"):
        assert private not in material
    assert "本端账号" in material and "逐笔交易的本方账号" in material
    assert before == database.q_raw("select * from import_sessions where import_id=?", [view["import_id"]])
    assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 0


@pytest.mark.parametrize("case_kind, state, reason", [
    ("abstention_with_unresolved_competitor", "pending", "no_support"),
    ("unresolved_competitor", "pending", "no_support"),
    ("missing_citations", "pending", "no_support"),
    ("unknown_fact", "pending", "no_support"),
    ("malformed", "unavailable", "provider_error"),
])
def test_comparison_difference_and_missing_support_do_not_call_recheck(client, make_case, monkeypatch, case_kind, state, reason):
    _, route, body = import_trial(client, make_case())

    def first(tag, view):
        if case_kind == "abstention_with_unresolved_competitor":
            return column_choices(view, ("unknown", "unknown") if tag == "d3-nano" else ("supported", "unknown"))
        if case_kind == "unresolved_competitor":
            return column_choices(view, ("supported", "unknown"))
        if case_kind == "missing_citations":
            return column_choices(view, cite=False)
        if case_kind == "unknown_fact":
            return column_choices(view, ("unknown", "unknown"))
        return {"C42": {"finding": "supported", "evidence": ["C42", "N3"]}}

    stub = ModelStub(first)
    configure(client, monkeypatch, stub)
    response = client.post(route, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["state"], result["reason"], result["first_calls"], result["recheck_calls"]) == (state, reason, 2, 0)
    assert result["packet"]["selection"] is None


def test_import_cited_fact_against_abstention_reaches_recheck(client, make_case, monkeypatch):
    case = make_case()
    _, route, body = import_trial(client, case)

    def first(tag, view):
        return column_choices(view, ("unknown", "refuted")) if tag == "d3-nano" else column_choices(view)

    def recheck(view, previous, conflicts):
        assert previous[0]["C1"] == {"finding": "supported", "evidence": ["C1", "N3"]}
        assert previous[1]["C1"] == {"finding": "unknown", "evidence": []}
        assert conflicts == ["C1"]
        return column_choices(view)

    configure(client, monkeypatch, ModelStub(first, recheck))
    response = client.post(route, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["state"], result["reason"], result["first_calls"], result["recheck_calls"]) == (
        "proposal", "recheck_supported", 2, 1)
    assert result["packet"]["selection"]["refs"] == ["C1", "N3"]
    assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 0


@pytest.mark.parametrize("scenario", ["default", "disabled", "no_read", "read", "outside", "second_read", "wrong_note", "changed"])
def test_import_review_reads_only_one_current_original_row(client, make_case, monkeypatch, scenario):
    original = ("字段口径：甲账户是逐笔交易本方账号；乙账户只用于历史档案查阅。\n"
                "甲账户,乙账户,交易时间,对方账号,收付标志,交易金额,交易币种,交易流水号,凭证号\n"
                "000812,000812,2036-08-26 10:00:00,SYN-B,入,2.11,CNY,SYN-1,SYN-1\n"
                "000812,000812,2036-08-26 10:01:00,SYN-C,出,3.22,CNY,SYN-2,SYN-2\n"
                "000812,000913,2036-08-26 10:02:00,SYN-D,入,4.33,CNY,SYN-3,SYN-3\n").encode()
    case = make_case()
    configure(client, monkeypatch)
    preview, route, body = import_trial(client, case, original)
    assert not preview["layout_applied"] and preview["table_summary"]["accepted"] == 0
    body["prepare_layout"] = True
    default_review = scenario in {"default", "disabled"}
    if scenario != "default":
        route += "?experimental_review_read=" + ("false" if scenario == "disabled" else "true")

    def first(tag, view):
        assert "read_offers" not in view and "added_original" not in view
        return column_choices(view, ("unknown", "refuted")) if tag == "d3-nano" else column_choices(view)

    def recheck(view, previous, conflicts):
        assert previous[1]["C1"] == {"finding": "unknown", "evidence": []}
        assert conflicts == ["C1"]
        if "added_original" not in view:
            if default_review:
                assert "read_offers" not in view
                return column_choices(view)
            assert list(view["read_offers"]) == ["L5"]
            if scenario == "no_read":
                return column_choices(view, ("unknown", "refuted"))
            if scenario == "outside":
                return {"read": "L999"}
            return {"read": "L5"}
        assert "read_offers" not in view
        extended = view["added_original"]
        assert extended["position"] == "L5"
        assert (extended["evidence"]["表头原行"], extended["evidence"]["原行"]) == (2, 5)
        cells = extended["evidence"]["候选列样值"]
        assert [item["物理列"] for item in cells] == [1, 2]
        assert [item["相同原值组"] for item in cells] == ["V1", "V2"]
        assert all(item["有前导零"] for item in cells)
        assert "000812" not in str(view) and "000913" not in str(view)
        if scenario == "second_read":
            return {"read": "L5"}
        if scenario == "changed":
            imports._path(preview["import_id"], ".csv").write_bytes(original.replace(b"000913", b"000999"))
        return column_choices(view, cite=scenario != "wrong_note")

    stub = ModelStub(first, recheck)
    configure(client, monkeypatch, stub)
    output = client.post(route, json=body)
    if scenario == "changed":
        assert output.status_code == 409 and output.json()["error"]["code"] == "source_changed"
        assert [tag for tag, _ in stub.calls].count("qwen35-4b") == 2
        assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 0
        return
    answer = output.raise_for_status().json()
    terminal = {
        "default": ("proposal", "recheck_supported", 1),
        "disabled": ("proposal", "recheck_supported", 1),
        "no_read": ("pending", "recheck_unresolved", 1),
        "read": ("proposal", "recheck_supported", 2),
        "outside": ("pending", "review_read_outside_scope", 1),
        "second_read": ("pending", "review_read_limit", 2),
        "wrong_note": ("pending", "recheck_unresolved", 2),
    }
    assert (answer["state"], answer["reason"], answer["recheck_calls"]) == terminal[scenario]
    assert answer["first_calls"] == 2
    read = scenario in {"read", "second_read", "wrong_note"}
    assert answer["packet"]["facts"].get("review_read") == ("L5" if read else None)
    accepted = default_review or scenario == "read"
    assert bool(answer["packet"]["selection"]) == accepted
    base = f"/api/v1/cases/{case}"
    assert client.get(base + "/flows").json()["total"] == 0
    if not accepted:
        assert answer["prepared_preview"] is None
        return
    prepared = answer["prepared_preview"]
    assert prepared["layout_model_assisted"] and prepared["table_summary"]["accepted"] == 3
    address = base + f"/imports/{preview['import_id']}"
    confirmation = {"mode": prepared["mode"], "mapping": prepared["mapping"], "mark_accounts": [],
                    "ready_revision": prepared["layout_review"]["revision"]}
    client.post(address + "/commit", json=confirmation).raise_for_status()
    receipt = poll(client, case, preview["import_id"])["result"]
    assert receipt["ok"] == 3 and receipt["layout_decision"]["origin"] == "model_assisted"
    assert {(row["payer_account"], row["payee_account"], row["amount"])
            for row in client.get(base + "/flows").json()["items"]} == {
                ("SYN-B", "000812", "2.11"), ("000812", "SYN-C", "3.22"), ("SYN-D", "000812", "4.33")}
    source = base + f"/sources/{preview['import_id']}"
    assert client.get(source + "/file").content == original
    client.delete(source + "/operation", params={"expected_batch_id": receipt["batch_id"]}).raise_for_status()
    assert client.get(base + "/flows").json()["total"] == 0


@pytest.mark.parametrize("cite_note", [False, True])
def test_import_note_only_support_requires_citation_of_the_associated_note(client, make_case, monkeypatch, cite_note):
    note = ("口径说明：甲账户是归集备用本方账号；乙账户是交易本方账号。\n"
            "甲账户,乙账户\nSYN-A,SYN-A\nSYN-B,SYN-B\n").encode()
    _, route, body = import_trial(client, make_case(), note)

    def first(_, view):
        [original_note] = view["notes"]
        assert "乙账户" in view["records"][original_note]["说明"][0]
        return column_choices(view, ("refuted", "supported"), cite=cite_note)

    configure(client, monkeypatch, ModelStub(first))
    response = client.post(route, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["first_calls"] == 2 and result["recheck_calls"] == 0
    if cite_note:
        assert result["state"] == "proposal"
        selection = result["packet"]["selection"]
        assert selection["choice"] == "C2" and len(selection["refs"]) == 2
        notes = {row["id"] for row in result["packet"]["records"] if row["id"].startswith("N")}
        assert notes & set(selection["refs"])
    else:
        assert result["state"] == "pending" and result["packet"]["selection"] is None


@pytest.mark.parametrize("competitor_resolved", [True, False])
def test_import_cited_opposite_original_fact_can_recheck_but_not_write(client, make_case, monkeypatch, competitor_resolved):
    case = make_case()
    _, route, body = import_trial(client, case)

    def first(tag, view):
        competitor = "refuted" if competitor_resolved else "unknown"
        return column_choices(view, ("refuted", competitor)) if tag == "d3-nano" else column_choices(view)

    def recheck(view, previous, conflicts):
        assert len(previous) == 2
        assert previous[0]["C1"] == {"finding": "supported", "evidence": ["C1", "N3"]}
        assert previous[1]["C1"] == {"finding": "refuted", "evidence": ["C1", "N3"]}
        expected = ["C1"]
        if not competitor_resolved:
            expected.append("C2")
        assert conflicts == expected
        return column_choices(view, ("supported", "unknown"))

    configure(client, monkeypatch, ModelStub(first, recheck))
    output = client.post(route, json=body)
    assert output.status_code == 200, output.text
    result = output.json()
    assert result["recheck_calls"] == 1
    if competitor_resolved:
        assert (result["state"], result["reason"]) == ("proposal", "recheck_supported")
        assert result["packet"]["selection"]["refs"] == ["C1", "N3"]
        assert result["column_checks"][1]["finding"] == "refuted"
    else:
        assert (result["state"], result["reason"]) == ("pending", "recheck_unresolved")
        assert result["packet"]["selection"] is None
    assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 0


@pytest.mark.parametrize("note", [
    "字段口径：甲账户是本方账号。"+ "填" * 160 + "甲账户只用于查询，乙账户才是本方账号。",
    "字段口径：甲账户是本方账号，并非备用账户。乙账户只用于档案。",
    "字段口径：甲账户不是代理人账号。乙账户是归集备用本方账号。",
])
def test_partial_or_unparsed_original_declaration_cannot_support_an_import_col(client, make_case, monkeypatch, note):
    raw = (note + "\n甲账户,乙账户\nSYN-A,SYN-B\nSYN-A2,SYN-B2\n").encode()
    _, route, body = import_trial(client, make_case(), raw)
    configure(client, monkeypatch, ModelStub(lambda *_: pytest.fail("no complete supporting declaration")))
    response = client.post(route, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "pending"
    assert response.json()["first_calls"] == 0 and response.json()["packet"]["selection"] is None


def test_trial_holds_a_registered_account_with_a_contrary_note_until_remapped(client, make_case, monkeypatch):
    raw = ("字段口径：交易账号只用于查询；本端账号是逐笔交易本方账号。\n"
           "交易时间,交易账号,本端账号,对方账号,交易金额,收付标志,交易币种\n"
           "2026-03-02 10:01:00,SYN-QUERY,SYN-REAL,SYN-OTHER,10.25,出,CNY\n").encode()
    case = make_case()

    with monkeypatch.context() as isolated:
        configure(client, isolated, ModelStub(lambda *_: pytest.fail("registered conflict must skip the models")))
        view, route, body = import_trial(client, case, raw)
        assert view["automatic"] and view["table_summary"]["unresolved"] == 0
        assert view["layout_model_trial"] and view["layout_model_trial_conflict"]
        report = client.post(route, json=body).json()
        assert (report["state"], report["reason"], report["first_calls"]) == ("pending", "source_role_conflict", 0)
        base = route.removesuffix("/layout-review/model-trial")
        blocked = client.post(base + "/commit", json={"mode": view["mode"], "mapping": view["mapping"]})
        assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "trial_source_role_conflict"
        physical = client.post(base + "/layout-review/apply", json={
            "revision": body["revision"], "confirmed": True, "regions": [{
                "member_path": "table.csv", "header_row": 2, "last_row": 3, "mode": "B",
                "mapping": {"trade_time": 1, "self_account": 2, "counter_account": 4,
                            "amount": 5, "direction": 6, "currency": 7},
            }],
        })
        assert physical.status_code == 409 and physical.json()["error"]["code"] == "trial_source_role_conflict"
        assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 0
        new = client.post(base + "/preview", json={"mode": view["mode"], "mapping": {
            **view["mapping"], "self_account": "本端账号"
        }})
        assert new.status_code == 200, new.text
        changed = new.json()
        assert not changed["layout_model_trial_conflict"]
        assert changed["preview"][0]["payer_account"] == "SYN-REAL"
        assert client.post(base + "/commit", json={"mode": changed["mode"], "mapping": changed["mapping"]}).status_code == 200
        done = poll(client, case, view["import_id"])
        assert done["stage"] == "done"
        [row] = client.get(f"/api/v1/cases/{case}/flows?granularity=observations").json()["items"]
        assert row["payer_account"] == "SYN-REAL"
    normal, _, _ = import_trial(client, make_case("Synthetic non-trial control"), raw)
    assert normal["automatic"] and not normal["layout_model_trial"] and not normal["layout_model_trial_conflict"]


def test_trial_checks_the_existing_field_note_sheet_before_committing(client, make_case, monkeypatch):
    book = Workbook()
    main = book.active
    main.title = "流水"
    main.append(["交易时间", "交易账号", "交易对手账卡号", "交易金额", "收付标志", "交易币种"])
    main.append(["2026-03-02 10:01:00", "SYN-LOOKUP", "SYN-OTHER", "10.25", "出", "CNY"])
    note = book.create_sheet("字段说明")
    note.append(["字段", "字段说明"])
    note.append(["流水!B1 交易账号", "查询条件"])
    content = BytesIO()
    book.save(content)
    case = make_case()
    configure(client, monkeypatch)
    view, route, _ = setup_review(client, case, content.getvalue(), "sample.xlsx")
    assert view["layout_model_trial"] and view["layout_model_trial_conflict"]
    blocked = client.post(route.removesuffix("/layout-review") + "/commit", json={"mode": view["mode"], "mapping": view["mapping"]})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "trial_source_role_conflict"
    assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 0


def test_only_verifiable_cited_pair_conflict_invokes_recheck(client, make_case, monkeypatch):
    route, body, packet = pair_trial(client, make_case())

    def first(tag, view):
        return model_choice(view["candidates"][0], "earlier" if tag == "d1-2b" else "later")

    def recheck(view, previous, conflicts):
        chosen = view["candidates"][0]
        assert previous == [model_choice(chosen, "earlier"), model_choice(chosen, "later")]
        assert conflicts == [{"row": chosen, "reported": "earlier", "reason": "cited_observation_dispute"}]
        return model_choice(view["candidates"][1], "later")

    stub = ModelStub(first, recheck)
    configure(client, monkeypatch, stub)
    response = client.post(route, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["state"], result["reason"], result["first_calls"], result["recheck_calls"]) == (
        "proposal", "recheck_supported", 2, 1)
    selection = result["packet"]["selection"]
    assert selection == {"status": "review_only", "choice": packet["candidates"][1]["id"],
                         "refs": [packet["candidates"][1]["id"]], "gap": None}
    material = json.dumps(stub.calls[0][1], ensure_ascii=False)
    for private in (packet["packet_sha256"], body["review_token"], "SYN-A", "SYN-FIRST-LATE", "record_url"):
        assert private not in material
    assert "independent_voucher_reference" not in material and "source_slip_reference" not in material


def test_verified_local_facts_on_competing_pair_candidates_reach_recheck(client, make_case, monkeypatch):
    case = make_case()
    route, body, _ = pair_trial(client, case)

    def first(tag, view):
        return model_choice(view["candidates"][int(tag == "d3-nano")], "later")

    def recheck(view, previous, conflicts):
        assert [item["answer"] for item in previous] == view["candidates"]
        assert [item["reason"] for item in conflicts] == ["competing_opposite_entries"] * 2
        return {"answer": "DEFER", "evidence": [], "finding": None}

    configure(client, monkeypatch, ModelStub(first, recheck))
    response = client.post(route, json=body)
    assert response.status_code == 200, response.text
    value = response.json()
    assert (value["state"], value["reason"], value["first_calls"], value["recheck_calls"]) == ("pending", "recheck_unresolved", 2, 1)
    assert value["receipt"]["status"] == "pending"


def test_same_day_midnight_cannot_supply_earlier_trade_time_evidence(client, make_case, monkeypatch):
    route, body, _ = pair_trial(client, make_case(), day_only=True)

    def first(_, view):
        uncertain = [ref for ref in view["candidates"] if view["records"][ref]["clock_precision"] == "day_or_midnight"]
        [chosen] = uncertain
        candidate = view["records"][chosen]
        anchor = view["records"][view["context"]["anchor"]]
        assert candidate["clock_order"] is None and candidate["clock_day"] == anchor["clock_day"]
        assert candidate["reference_excerpt"] and anchor["reference_excerpt"]
        assert not candidate["reference_groups"] and not anchor["reference_groups"]
        return model_choice(chosen, "earlier")

    configure(client, monkeypatch, ModelStub(first))
    response = client.post(route, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["state"], result["reason"], result["first_calls"], result["recheck_calls"]) == (
        "pending", "no_support", 2, 0)


def trace_trial(client, case, rows):
    found = import_books(client, case, rows)
    source, target = found["SYN-B-SOURCE"], found["SYN-B-TARGET"]
    context = review(client, case, target["id"], x=10)
    path, body, packet = ask_flow(client, case, target["id"], context, "trace_source", source["id"])
    return path.replace("review-assistance", "review-model-trial"), body, packet, found


def mixed_trace_rows(*, balances=True):
    return [
        book_row("B", "EXT", "B", "100.00", "140.00" if balances else "", 0, 1 if balances else "", "SOURCE"),
        book_row("B", "B", "D", "50.00", "90.00" if balances else "", 1, 2 if balances else "", "MIX-OUT"),
        book_row("B", "E", "B", "20.00", "110.00" if balances else "", 2 if balances else 1,
                 3 if balances else "", "MIX-IN"),
        book_row("B", "B", "C", "90.00", "20.00" if balances else "", 3 if balances else 1,
                 4 if balances else "", "TARGET"),
    ]


def test_trace_complete_interval_returns_all_original_rows_with_zero_model_calls(client, make_case, monkeypatch):
    case = make_case()
    configure(client, monkeypatch, ModelStub(lambda *_: pytest.fail("a verified interval bypasses models")))
    path, body, initial, found = trace_trial(client, case, mixed_trace_rows())
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["state"], result["reason"], result["first_calls"], result["recheck_calls"]) == (
        "rule", "ledger_range_available", 0, 0)
    packet = result["packet"]
    assert packet["records"] == initial["records"]
    assert packet["facts"]["product_review"]
    assert result["receipt"]["status"] == "accepted"
    assert (result["receipt"]["lower"], result["receipt"]["upper"]) == ("30.00", "90.00")
    assert packet["selection"] is None and packet["facts"]["attribution_status"] == "unknown"
    assert packet["facts"]["conditional_group_share"] == {"min": "30.00", "max": "90.00"}
    assert packet["facts"]["other_entries"] == {
        "in": {"count": 1, "amount": "20.00"}, "out": {"count": 1, "amount": "50.00"}}
    references = {*packet["facts"]["source_refs"], *packet["facts"]["target_refs"],
                  *(item["id"] for item in packet["candidates"])}
    assert references == {f"F{row['id']}" for row in found.values()}
    assert {row["id"] for row in packet["records"]} == references
    for record in packet["records"]:
        locator = record["locator"]
        assert client.get(locator["file_url"]).status_code == 200
        assert client.get(locator["record_url"]).json()["items"][0]["source_row"] == locator["source_row"]


def explanation_transport(client, monkeypatch, reply, *, model="qwen35-4b", tokens=42, during_generation=None):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (trial.parse_model_endpoints(ORIGINS),), raising=False)
    calls = []

    def request(_self, tag, route, body):
        assert tag == "qwen35-4b"
        calls.append((route, body))
        if route == "/apply-template":
            assert body["chat_template_kwargs"] == {"enable_thinking": False}
            prompt = body["messages"][0]["content"]
            assert not any(ch in prompt for ch in ("SYN-", "/api/", "file_url", "review_token", "packet_sha256"))
            return {"prompt": "<|im_start|>user\n" + prompt + "<|im_end|>\n" + trial._NO_THINK}
        if route == "/tokenize":
            assert body["parse_special"] is True and body["add_special"] is False
            return {"tokens": [1, 2]}
        assert route == "/completion" and body["prompt"] == [1, 2] and not body["cache_prompt"]
        assert body["json_schema"]["required"] == ["text", "refs"]
        pattern = body["json_schema"]["properties"]["text"]["pattern"]
        assert pattern.startswith("^") and pattern.endswith("(。)?$")
        if during_generation is not None:
            during_generation()
        return {"model": model, "tokens_predicted": tokens, "truncated": False, "stop_type": "eos",
                "content": json.dumps(reply, ensure_ascii=False)}

    monkeypatch.setattr(trial._LoopbackProvider, "_request", request)
    return calls


@pytest.mark.parametrize("text", [
    "这笔入账可覆盖本次支出中的¥30.00至90.00。",
    "本次支出能由这笔收入承担30.00到90.00元。",
])
def test_rule_trace_can_return_separate_validated_qwen_prose(client, make_case, monkeypatch, text):
    case = make_case()
    calls = explanation_transport(client, monkeypatch, {"text": text, "refs": ["R1", "R2"]})
    path, body, _, _ = trace_trial(client, case, mixed_trace_rows())
    default = client.post(path, json=body).json()
    assert not calls and default["explanation"] is None and default["explanation_calls"] == 0
    result = client.post(path, json={**body, "explain": True})
    assert result.status_code == 200, result.text
    value = result.json()
    assert value["receipt"] == default["receipt"]
    assert (value["first_calls"], value["recheck_calls"], value["explanation_calls"]) == (0, 0, 1)
    assert (value["explanation"], value["explanation_model"]) == (text, "qwen35-4b")
    assert [route for route, _ in calls] == ["/apply-template", "/tokenize", "/completion"]
    prompt = calls[0][1]["messages"][0]["content"]
    assert text not in prompt
    facts = json.loads(prompt.splitlines()[-1])
    assert (facts["下限"], facts["上限"], facts["币种"]) == ("30.00", "90.00", "CNY")
    assert facts["引文编号及角色"] == {"R1": "source", "R2": "target"}
    stored = client.get(f"/api/v1/cases/{case}/model-trial/receipts/{value['receipt']['receipt_id']}").json()
    assert stored["authority"] == "historical" and stored["receipt"] == value["receipt"]
    assert "explanation" not in stored


@pytest.mark.parametrize("amount,after,refs,model,tokens,count", [
    ("USD 30.00至90.00", "", ["R1", "R2"], "qwen35-4b", 50, 1),
    ("CNY 30.01至90.00", "", ["R1", "R2"], "qwen35-4b", 50, 1),
    ("CNY 90.00至30.00", "", ["R1", "R2"], "qwen35-4b", 50, 1),
    ("CNY 30.00至90.00", "实际资金已确定。", ["R1", "R2"], "qwen35-4b", 50, 1),
    ("CNY 30.00至90.00", "", ["R1", "P-C06"], "qwen35-4b", 50, 1),
    ("CNY 30.00至90.00", "", ["R1", "R1"], "qwen35-4b", 50, 1),
    ("CNY 30.00至90.00", "", ["R1", "R2"], "another-model", 50, 0),
    ("CNY 30.00至90.00", "", ["R1", "R2"], "qwen35-4b", 0, 0),
])
def test_trace_rejects_invalid_or_unverified_explanation_without_changing_receipt(
        client, make_case, monkeypatch, amount, after, refs, model, tokens, count):
    case = make_case()
    sentence = f"这笔入账可为本次支出提供{amount}。{after}"
    calls = explanation_transport(client, monkeypatch, {"text": sentence, "refs": refs}, model=model, tokens=tokens)
    path, body, _, _ = trace_trial(client, case, mixed_trace_rows())
    response = client.post(path, json={**body, "explain": True})
    assert response.status_code == 200, response.text
    value = response.json()
    assert (value["explanation"], value["explanation_model"], value["explanation_calls"]) == (None, None, count)
    assert [route for route, _ in calls] == ["/apply-template", "/tokenize", "/completion"]
    assert (value["receipt"]["status"], value["receipt"]["currency"], value["receipt"]["lower"],
            value["receipt"]["upper"]) == ("accepted", "CNY", "30.00", "90.00")


@pytest.mark.parametrize("text,approved", [
    ("本方余额还没核清，还不能确定这笔钱从哪来。", True),
    ("本方余额还没核清，所选范围为 CNY 1.00。", False),
])
def test_pending_trace_receives_only_the_verified_gap_and_source_roles(client, make_case, monkeypatch, text, approved):
    case = make_case()
    calls = explanation_transport(client, monkeypatch, {"text": text, "refs": ["R1"]})
    path, body, _, _ = trace_trial(client, case, mixed_trace_rows(balances=False))
    response = client.post(path, json={**body, "explain": True})
    assert response.status_code == 200, response.text
    value = response.json()
    assert (value["receipt"]["status"], value["receipt"]["reason"]) == ("pending", "ledger_evidence_missing")
    assert (value["first_calls"], value["recheck_calls"], value["explanation_calls"]) == (0, 0, 1)
    assert (value["explanation"], value["explanation_model"]) == ((text, "qwen35-4b") if approved else (None, None))
    facts = json.loads(calls[0][1]["messages"][0]["content"].splitlines()[-1])
    assert facts["状态"] == "未决" and "币种" not in facts and "下限" not in facts and "上限" not in facts


@pytest.mark.parametrize("kind,checks,reasons,text", [
    ("trace_source", {"own_balance": False}, [], "本方余额还没核清，还不能确定这笔钱从哪来。"),
    ("trace_target", {"order": False}, [], "交易先后还没核清，还不能确定这笔钱去了哪里。"),
    ("trace_source", {"interval_records": False}, [], "这段期间的流水还没核齐，目前还查不清这笔钱从哪来。"),
    ("trace_source", {"interval_records": False}, ["unresolved_source_records"],
     "案内仍有来源记录待核，还不能确定这笔钱从哪来。"),
])
def test_pending_explanation_uses_the_actual_check_and_trace_direction(kind, checks, reasons, text):
    from app.services import trace_explanation

    facts = {"kind": kind, "source_refs": ["F1"], "target_refs": ["F2"], "source_count": 1, "target_count": 1,
             "checks": checks, "reasons": reasons}
    receipt = {"proposition": "conditional_range", "status": "pending", "reason": "no_support"}
    request = trace_explanation.prepare({"facts": facts}, receipt, {"F1", "F2"})
    assert request.validated_text({"text": text, "refs": ["R1"]}) == text
    wrong = text.replace("从哪来", "去了哪里") if kind == "trace_source" else text.replace("去了哪里", "从哪来")
    assert request.validated_text({"text": wrong, "refs": ["R1"]}) is None
    assert request.validated_text({"text": "全部原件都没有，" + text.split("，")[1], "refs": ["R1"]}) is None


@pytest.mark.parametrize("bounds,text,bad", [
    (("0.00", "0.40"), "这笔入账最多可覆盖本次支出中的 ¥0.40。", "这笔入账至少可覆盖本次支出中的 ¥0.40。"),
    (("0.00", "0.40"), "这笔入账最多能为本次支出提供0.40元。", "这笔入账能为本次支出提供0.40元。"),
    (("0.00", "0.40"), "这笔支出至多可由这笔入账承担CNY 0.40。", "这笔入账至多可由这笔支出承担CNY 0.40。"),
    (("0.40", "0.40"), "从现有账目看，这笔入账可为本次支出提供0.40元。", "本次支出已经确定来自这笔入账0.40元。"),
    (("0.00", "0.00"), "这笔入账不能为本次支出提供资金。", "这笔入账可为本次支出提供0.40元。"),
    (("0.30", "0.40"), "这笔入账可覆盖本次支出中的0.30至0.40元。", "这笔入账可覆盖本次支出中的0.40至0.30元。"),
])
def test_explanation_distinguishes_each_approved_amount_shape(bounds, text, bad):
    from app.services import trace_explanation

    facts = {"kind": "trace_source", "source_refs": ["F1"], "target_refs": ["F2"], "source_count": 1, "target_count": 1}
    receipt = {"proposition": "conditional_range", "status": "accepted", "refs": ["F1", "F2"],
               "currency": "CNY", "lower": bounds[0], "upper": bounds[1]}
    specification = trace_explanation.prepare({"facts": facts}, receipt, {"F1", "F2"})
    assert specification.validated_text({"text": text, "refs": ["R1", "R2"]}) == text
    assert specification.validated_text({"text": bad, "refs": ["R1", "R2"]}) is None
    assert text not in specification.material
    if bounds == ("0.00", "0.40"):
        sent = json.loads(specification.material.splitlines()[-1])
        assert sent["上限"] == "0.40" and "下限" not in sent


def test_explanation_rechecks_live_original_after_native_generation(client, make_case, monkeypatch):
    case = make_case()
    text = "这笔入账可以覆盖本次支出中的CNY30.00至90.00。"
    path, body, packet, _ = trace_trial(client, case, mixed_trace_rows())
    source_id = packet["records"][0]["locator"]["source_id"]
    source = imports._path(source_id, imports._source_session(case, source_id)["suffix"])

    def replace_original():
        before = source.read_bytes()
        source.write_bytes(bytes([before[0] ^ 1]) + before[1:])

    calls = explanation_transport(client, monkeypatch, {"text": text, "refs": ["R1", "R2"]}, during_generation=replace_original)
    changed = client.post(path, json={**body, "explain": True})
    assert changed.status_code == 409, changed.text
    assert [route for route, _ in calls] == ["/apply-template", "/tokenize", "/completion"]
    assert text not in changed.text


def test_explanation_is_trial_trace_only_and_unconfigured_trace_keeps_rule_receipt(client, make_case, monkeypatch):
    case = make_case()
    configure(client, monkeypatch)
    path, body, _, _ = trace_trial(client, case, mixed_trace_rows())
    response = client.post(path, json={**body, "explain": True})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["receipt"]["status"] == "accepted"
    assert (value["explanation"], value["explanation_model"], value["explanation_calls"]) == (None, None, 0)
    assert client.post(path.replace("review-model-trial", "review-assistance"),
                       json={**body, "explain": True}).status_code == 422
    assert client.post(path, json={**body, "kind": "pair", "explain": True}).status_code == 422


def test_joint_trace_has_a_case_bound_durable_receipt_for_the_complete_selection(client, make_case, monkeypatch):
    case = make_case()
    configure(client, monkeypatch)
    rows = [
        book_row("B", "EXT", "B", "100.00", "150.00", 0, 1, "SOURCE"),
        book_row("B", "B", "C", "75.00", "75.00", 1, 2, "TARGET"),
        book_row("B", "B", "D", "75.00", "0.00", 2, 3, "TARGET2"),
    ]
    found = import_books(client, case, rows)
    source = found["SYN-B-SOURCE"]["id"]
    targets = [found[f"SYN-B-{code}"]["id"] for code in ("TARGET", "TARGET2")]
    card = review(client, case, source, x=10)
    route = f"/api/v1/cases/{case}/review-model-trial"
    body = {"kind": "trace_target", "review_token": card["review"]["token"], "anchor_id": source, "canvas_id": "0"}
    single = client.post(route, json={**body, "group_flow_id": targets[0]}).json()["receipt"]
    joined = client.post(route, json={**body, "group_flow_ids": targets})
    assert joined.status_code == 200, joined.text
    actual = joined.json()["receipt"]
    assert (actual["status"], actual["lower"], actual["upper"], actual["ledger_rows"]) == (
        "accepted", "100.00", "100.00", 3)
    assert actual["receipt_id"] != single["receipt_id"]
    receipt_path = f'/api/v1/cases/{case}/model-trial/receipts/{actual["receipt_id"]}'
    original_receipt = client.get(receipt_path).json()
    assert original_receipt["authority"] == "historical"
    assert original_receipt["receipt"] == actual
    assert {item["source_row"] for item in original_receipt["originals"]} == {2, 3, 4}
    repeat = review(client, case, source, x=8)
    reordered = client.post(route, json={**body, "review_token": repeat["review"]["token"], "group_flow_ids": targets[::-1]})
    assert reordered.json()["receipt"]["receipt_id"] == actual["receipt_id"]
    assert client.get(receipt_path).json() == original_receipt
    bad = client.post(route, json={**body, "group_flow_ids": [targets[0], source]})
    assert bad.status_code == 422
    other = make_case("Synthetic receipt scope control")
    assert client.get(receipt_path.replace(f"/{case}/model-trial", f"/{other}/model-trial")).status_code == 404
    assert client.delete(f"/api/cases/{case}").status_code == 200
    assert client.get(receipt_path).status_code == 404


def test_joint_sources_and_targets_share_the_same_book_capacity(client, make_case, monkeypatch):
    configure(client, monkeypatch)
    case = make_case()
    rows = [
        book_row("B", "S1", "B", "100.00", "100.00", 0, 1, "S1"),
        book_row("B", "S2", "B", "50.00", "150.00", 1, 2, "S2"),
        book_row("B", "OTHER", "B", "25.00", "175.00", 2, 3, "OTHER"),
        book_row("B", "B", "T1", "40.00", "135.00", 3, 4, "T1"),
        book_row("B", "B", "T2", "10.00", "125.00", 4, 5, "T2"),
    ]
    ledger = import_books(client, case, rows)
    names = {label: ledger[f"SYN-B-{label}"]["id"] for label in ("S1", "S2", "T1", "T2")}
    opened = review(client, case, names["T1"], x=10)
    route = f"/api/v1/cases/{case}/review-model-trial"
    body = {"kind": "trace_source", "review_token": opened["review"]["token"], "anchor_id": names["T1"], "canvas_id": "0",
            "source_flow_ids": [names["S1"], names["S2"]], "target_flow_ids": [names["T1"], names["T2"]]}
    accepted = client.post(route, json=body)
    assert accepted.status_code == 200, accepted.text
    result = accepted.json()
    receipt = result["receipt"]
    assert result["packet"]["facts"]["ledger_row_count"] == 5
    assert (receipt["status"], receipt["lower"], receipt["upper"]) == ("accepted", "25.00", "50.00")
    wrong = client.post(route, json={**body, "source_flow_ids": [names["T2"]], "target_flow_ids": [names["T1"]]})
    assert wrong.status_code == 422
    assert wrong.json()["error"]["code"] == "assistance_wrong_ledger_direction"


def test_trace_exposes_verified_time_exclusion_without_a_monetary_range(client, make_case, monkeypatch):
    case = make_case()
    configure(client, monkeypatch, ModelStub(lambda *_: pytest.fail("known time order needs no model")))
    ledger = import_books(client, case, [
        book_row("B", "B", "T", "40.00", "", 0, 1, "TARGET"),
        book_row("B", "S", "B", "40.00", "", 1, 2, "SOURCE"),
    ])
    credit = ledger["SYN-B-SOURCE"]["id"]
    debit = ledger["SYN-B-TARGET"]["id"]
    card = review(client, case, debit, x=10)
    url = f"/api/v1/cases/{case}/review-model-trial"
    body = {"kind": "trace_source", "review_token": card["review"]["token"], "anchor_id": debit,
            "canvas_id": "0", "source_flow_ids": [credit], "target_flow_ids": [debit]}
    value = client.post(url, json=body).raise_for_status().json()
    assert (value["state"], value["reason"], value["first_calls"], value["recheck_calls"]) == (
        "pending", "trace_time_exclusion", 0, 0)
    assert value["receipt"]["status"] == "pending" and value["receipt"]["upper"] is None
    assert value["packet"]["facts"]["conditional_group_share"] is None
    refs = {*value["packet"]["facts"]["source_refs"], *value["packet"]["facts"]["target_refs"]}
    originals = [row["locator"] for row in value["packet"]["records"] if row["id"] in refs]
    assert {loc["source_row"] for loc in originals} == {2, 3}
    assert all(client.get(loc["file_url"]).is_success for loc in originals)

    origin = originals[0]["source_id"]
    file = imports._path(origin, imports._source_session(case, origin)["suffix"])
    file.write_bytes(b"X" * file.stat().st_size)
    inaccessible = client.post(url, json=body).raise_for_status().json()
    assert inaccessible["reason"] == "source_unavailable"
    assert inaccessible["receipt"]["upper"] is None


@pytest.mark.parametrize("account", ["00070101", "00000000"])
def test_query_book_blank_peer_needs_a_distinct_original_account(client, make_case, monkeypatch, account):
    from app import table_source_roles
    from test_automatic_table_import import preview
    from test_feedback_import_api import complete
    from test_uniform35_reference import row, workbook

    case = make_case("Synthetic query-book directions")
    configure(client, monkeypatch, ModelStub(lambda *_: pytest.fail("No interval proof to send")))
    rows = [row(**{"查询帐号": account, "查询卡号": "00001201", "交易对方账卡号": "",
                   "交易对方名称": "", "借贷标志": marker, "币种": "CNY", "交易金额": "30.00",
                   "交易时间": clock, "交易流水号": ref, "凭证号": ref, "交易是否成功": "成功"})
            for marker, clock, ref in (("贷", "20460113090000", "SYN-IN"), ("借", "20460113100000", "SYN-OUT"))]
    view = preview(client, case, workbook(rows, headers=table_source_roles._TRANSACTION_QUERY_ONLY), "query-book.xlsx")
    assert complete(client, case, view)["stage"] == "done"
    root = f"/api/v1/cases/{case}"
    records = client.get(f"{root}/imports/{view['import_id']}/records").raise_for_status().json()["items"]
    incoming, outgoing = [str(entry["flow_id"]) for entry in records]
    card = client.get(f"/api/context/{outgoing}", params={"case_id": case, "side": "payer", "review": "true"}).raise_for_status().json()
    body = {"kind": "trace_source", "review_token": card["review"]["token"], "anchor_id": outgoing,
            "canvas_id": "0", "source_flow_ids": [incoming], "target_flow_ids": [outgoing]}
    response = client.post(f"{root}/review-model-trial", json=body)
    if account == "00000000":
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "assistance_wrong_ledger_direction"
    else:
        result = response.raise_for_status().json()
        assert result["packet"]["facts"]["checks"]["ledger_roles"]
        assert result["packet"]["facts"]["conditional_group_share"] is None
        assert (result["first_calls"], result["recheck_calls"]) == (0, 0)


def test_accepted_trace_does_not_save_a_receipt_when_the_original_changes_before_recording(client, make_case, monkeypatch):
    from app.services import trial_review_receipts

    case = make_case()
    configure(client, monkeypatch)
    route, body, initial, _ = trace_trial(client, case, mixed_trace_rows())
    source_id = initial["records"][0]["locator"]["source_id"]
    session = imports._source_session(case, source_id)
    original = imports._path(source_id, session["suffix"])
    recorded = {}
    save = trial_review_receipts.record

    def changed_source(cid, scope, result, tickets):
        recorded["id"] = result["receipt"]["receipt_id"]
        bytes_before = original.read_bytes()
        original.write_bytes(bytes([bytes_before[0] ^ 1]) + bytes_before[1:])
        return save(cid, scope, result, tickets)

    monkeypatch.setattr(trial_review_receipts, "record", changed_source)
    rejected = client.post(route, json=body)
    assert rejected.status_code == 409, rejected.text
    assert client.get(f'/api/v1/cases/{case}/model-trial/receipts/{recorded["id"]}').status_code == 404


@pytest.mark.parametrize("embedded_offset", [False, True])
def test_trace_does_not_ignore_a_separate_transaction_clock_column(client, make_case, monkeypatch, embedded_offset):
    configure(client, monkeypatch)
    raw = (
        ("EXT", "B", "100.00", "100.00", "10:00:00", "+00:00", "SOURCE"),
        ("B", "C", "100.00", "0.00", "10:01:00", "+00:00", "TARGET"),
        ("B", "D", "100.00", "0.00", "11:00:10", "+01:00", "COMPETING-DEBIT"),
        ("E", "B", "100.00", "100.00", "11:00:20", "+01:00", "COMPETING-CREDIT"),
    )
    rows = []
    for number, (payer, payee, amount, balance, clock, offset, ref) in enumerate(raw):
        row = book_row("B", payer, payee, amount, balance, number, number + 1, ref)
        row["交易时间"] = f"2026-01-01T{clock}{offset if embedded_offset else ''}"
        row["交易时区"] = offset
        row.pop("记账顺序号")
        rows.append(row)
    path, body, _, _ = trace_trial(client, make_case(), rows)
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["first_calls"] == 0
    if embedded_offset:
        assert value["receipt"]["status"] == "accepted"
        assert (value["receipt"]["lower"], value["receipt"]["upper"]) == ("0.00", "0.00")
    else:
        assert value["reason"] == "source_clock_scope_unproved"
        assert value["receipt"]["status"] == "pending"
        assert value["receipt"]["upper"] is None


def test_trace_no_ledger_balance_or_order_stays_pending_without_model_calls(client, make_case, monkeypatch):
    path, body, _, _ = trace_trial(client, make_case(), mixed_trace_rows(balances=False))
    configure(client, monkeypatch, ModelStub(lambda *_: pytest.fail("missing ledger evidence cannot be inferred")))
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["state"], result["reason"], result["first_calls"], result["recheck_calls"]) == (
        "pending", "ledger_evidence_missing", 0, 0)
    packet = result["packet"]
    assert packet["facts"]["conditional_group_share"] is None and packet["selection"] is None
    assert not packet["facts"]["checks"]["order"] and not packet["facts"]["checks"]["own_balance"]
    assert all(record["raw"]["balance"] is None for record in packet["records"])


def test_trace_damaged_original_cannot_enable_direct_rule_or_model_calls(client, make_case, monkeypatch):
    case = make_case()
    path, body, packet, _ = trace_trial(client, case, mixed_trace_rows())
    configure(client, monkeypatch, ModelStub(lambda *_: pytest.fail("a broken original cannot be sent to a model")))
    row = next(record for record in packet["records"] if record["id"] == packet["facts"]["source_refs"][0])
    locator = row["locator"]
    source = locator["source_id"]
    original = imports._path(source, imports._source_session(case, source)["suffix"])
    original.write_bytes(b"X" * original.stat().st_size)
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["state"], result["reason"], result["first_calls"], result["recheck_calls"]) == (
        "pending", "source_unavailable", 0, 0)
    assert result["packet"]["facts"]["conditional_group_share"] == {"min": "30.00", "max": "90.00"}
    assert result["packet"]["selection"] is None
    assert client.get(locator["file_url"]).status_code == 409


def test_trace_incomplete_balances_can_leave_distinguishable_model_leads(client, make_case, monkeypatch):
    rows = mixed_trace_rows()
    rows[-1]["本笔交易后账面余额"] = ""
    path, body, _, found = trace_trial(client, make_case(), rows)

    def first(_, view):
        [chosen] = [ref for ref in view["candidates"] if view["records"][ref]["amount"] == "50.00"]
        row = view["records"][chosen]
        assert row["declared_direction"] == "unknown" and row["book_direction"] == "out"
        assert row["account_groups"]["self"] == row["account_groups"]["payer"]
        assert "posting_order" not in row
        assert view["context"]["opening_balance"] is None and view["context"]["closing_balance"] is None
        return model_choice(chosen, "out")

    configure(client, monkeypatch, ModelStub(first))
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["state"], result["reason"], result["first_calls"], result["recheck_calls"]) == (
        "proposal", "first_agreement", 2, 0)
    assert result["packet"]["facts"]["conditional_group_share"] is None
    assert result["packet"]["facts"]["attribution_status"] == "unknown"
    assert result["packet"]["selection"]["choice"] == f"F{found['SYN-B-MIX-OUT']['id']}"


def test_trace_accepts_complete_originals_with_a_truncated_reference_window(client, make_case, monkeypatch):
    rows = [book_row("B", "EXT", "B", "100.00", "100.00", 0, 1, "SOURCE"),
            *(book_row("B", "B", f"X{i}", "1.00", f"{100 - i}.00", i, i + 1, f"MIX-{i}") for i in range(1, 8)),
            book_row("B", "B", "C", "90.00", "3.00", 8, 9, "TARGET")]
    configure(client, monkeypatch)
    path, body, initial, _ = trace_trial(client, make_case(), rows)
    assert initial["facts"]["conditional_group_share"] == {"min": "90.00", "max": "90.00"}
    assert initial["facts"]["more_evidence"] is True
    assert initial["facts"]["other_entries"]["out"] == {"count": 7, "amount": "7.00"}
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["state"], result["reason"], result["first_calls"], result["recheck_calls"]) == (
        "rule", "ledger_range_available", 0, 0)
    assert result["packet"]["selection"] is None
    receipt = result["receipt"]
    assert receipt["ledger_rows"] == 9 and receipt["evidence_window_truncated"]
    assert (receipt["lower"], receipt["upper"]) == ("90.00", "90.00")


def test_confirmed_pair_exposes_a_separate_book_entry_without_confirming_another_event(client, make_case, monkeypatch):
    configure(client, monkeypatch)
    case = make_case()
    found = import_books(client, case, [
        book_row("A", "A", "B", "37.41", "962.59", 0, 1, "FIRST"),
        book_row("B", "A", "B", "37.41", "2037.41", 0, 1, "FIRST"),
        book_row("B", "A", "B", "37.41", "2074.82", 1, 2, "SECOND"),
    ])
    anchor, peer, distinct = (found[key]["id"] for key in ("SYN-A-FIRST", "SYN-B-FIRST", "SYN-B-SECOND"))
    view = review(client, case, anchor, x=10)
    body = {"review_token": view["review"]["token"], "canvas_id": "0", "anchor_id": anchor, "kind": "pair"}
    checked = client.post(f"/api/v1/cases/{case}/review-model-trial", json=body)
    assert checked.status_code == 200, checked.text
    value = checked.json()
    assert value["first_calls"] == value["recheck_calls"] == 0
    assert value["receipt"]["observations"] == 2
    assert set(value["receipt"]["refs"]) == {f"F{anchor}", f"F{peer}"}
    [pending] = value["packet"]["facts"]["open_book_postings"]
    assert pending == {"ref": f"F{distinct}", "witness_ref": f"F{peer}", "reference": "SECOND",
                       "reason": "book_posting_needs_opposite_ledger"}
    row = next(item for item in value["packet"]["records"] if item["id"] == pending["ref"])
    assert row["locator"]["source_row"] == 4


def test_rule_bypasses_models_and_needs_unchanged_original_bytes(client, make_case, monkeypatch):
    case = make_case()
    route, body, packet = pair_trial(client, case, confirmed=True)
    stub = ModelStub(lambda *_: pytest.fail("recorded proof must bypass first models"))
    configure(client, monkeypatch, stub)
    initial = client.post(route, json=body).json()
    assert (initial["state"], initial["reason"], initial["first_calls"]) == ("rule", "rule_confirmed", 0)
    source = next(record["locator"]["source_id"] for record in packet["records"] if record["locator"].get("source_id"))
    original = imports._path(source, ".csv")
    original.write_bytes(b"X" * original.stat().st_size)
    out = client.post(route, json=body).json()
    assert (out["state"], out["reason"], out["first_calls"]) == ("pending", "source_unavailable", 0)
    assert out["packet"]["facts"]["rule_status"] == "confirmed" and out["packet"]["selection"] is None
    assert client.get(f"/api/v1/cases/{case}/sources/{source}/file").status_code == 409


def test_revocation_between_first_models_prevents_second_submission(client, make_case, monkeypatch):
    case = make_case()
    route, body, _ = pair_trial(client, case)

    def first(tag, view):
        assert tag == "d1-2b"
        closed = client.delete("/api/context/review", params={"case_id": case},
                               headers={"X-FundsTrace-Review": body["review_token"]})
        assert closed.status_code in (200, 204)
        return model_choice(view["candidates"][0], "later")

    stub = ModelStub(first)
    configure(client, monkeypatch, stub)
    response = client.post(route, json=body)
    assert response.status_code == 409, response.text
    assert [tag for tag, _ in stub.calls] == ["d1-2b"]


def test_changed_original_while_second_model_runs_vetoes_late_advice(client, make_case, monkeypatch):
    view, route, body = import_trial(client, make_case())
    original = imports._path(view["import_id"], ".csv")

    def first(tag, packet):
        if tag == "d3-nano":
            original.write_bytes(TRIAL_CSV.replace(b"SYN-LEAD-PRIVATE", b"SYN-FAKE-PRIVATE"))
        return column_choices(packet)

    configure(client, monkeypatch, ModelStub(first))
    response = client.post(route, json=body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "source_changed"


def test_qwen_uses_verified_non_thinking_template_and_bounded_completion(monkeypatch):
    calls = []

    def request(tag, route, body):
        assert tag == "qwen35-4b"
        calls.append(route)
        if route == "/apply-template":
            content = body["messages"][0]["content"]
            assert body["chat_template_kwargs"] == {"enable_thinking": False}
            return {"prompt": "<|im_start|>user\n" + content + "<|im_end|>\n" + trial._NO_THINK}
        if route == "/tokenize":
            assert body["parse_special"] is True
            return {"tokens": [1, 2]}
        assert body["cache_prompt"] is False and body["n_predict"] == 256
        return {"truncated": False, "stop_type": "eos", "content": json.dumps(model_choice("R1", "later"))}

    provider = trial._LoopbackProvider(trial.parse_model_endpoints(ORIGINS))
    monkeypatch.setattr(provider, "_request", request)
    packet = {"domain": "pair", "records": {"R1": {}, "R2": {}}, "candidates": ["R1", "R2"]}
    assert provider.recheck(packet, [], []) == model_choice("R1", "later")
    assert calls == ["/apply-template", "/tokenize", "/completion"]


def test_model_transport_ignores_proxies_and_rejects_redirect_oversize_and_slow_body(monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            received.append(self.path)
            self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/redirect":
                self.send_response(307)
                self.send_header("Location", "/forbidden")
                self.end_headers()
                return
            self.send_response(200)
            if self.path == "/oversize":
                self.send_header("Content-Length", str(trial._MAX_BYTES + 1))
                self.end_headers()
            elif self.path == "/slow":
                self.end_headers()
                try:
                    for _ in range(80):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        sleep(0.02)
                except OSError:
                    pass
            else:
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("NO_PROXY", "")
        endpoints = trial.parse_model_endpoints({**ORIGINS, "d1-2b": f"http://127.0.0.1:{server.server_port}"})
        try:
            provider = trial._LoopbackProvider(endpoints)
            assert provider._request("d1-2b", "/ok", {}) == {"ok": True}
            for path in ("/redirect", "/oversize"):
                with pytest.raises(trial.ProviderError):
                    provider._request("d1-2b", path, {})
            monkeypatch.setattr(trial, "_SECONDS", 0.12)
            start = monotonic()
            with pytest.raises(trial.ProviderError):
                trial._LoopbackProvider(endpoints)._request("d1-2b", "/slow", {})
            assert monotonic() - start < 1.0
            assert received == ["/ok", "/redirect", "/oversize", "/slow"]
        finally:
            server.shutdown()
            worker.join()


def test_native_column_citation_repeats_the_decided_use_and_offers_original_note_text(monkeypatch):
    view = {
        "target": "每笔流水实际收支的本方账号",
        "candidates": ["C2", "C3", "C4"],
        "notes": ["N8", "N9"],
        "records": {
            "C2": {"表头": "本方保管账号", "原行": 2, "物理列": 2, "样本类型": ["text"]},
            "C3": {"表头": "已调单账户", "原行": 2, "物理列": 3, "样本类型": ["text"]},
            "C4": {"表头": "本方暂记账号", "原行": 2, "物理列": 4, "样本类型": ["text"]},
            "N8": {"说明": ["字段口径：已调单账户是逐笔交易本方账号；本方保管账号仅作文件保管用途。"],
                   "说明原行": 1, "完整": True},
            "N9": {"说明": ["附表说明：本方暂记账号是" + "样" * 90], "说明原行": 5, "完整": False},
        },
    }
    seen = []
    provider = trial._LoopbackProvider(trial.parse_model_endpoints(ORIGINS))

    def respond(tag, route, body):
        assert (tag, route) == ("d1-2b", "/v1/systemone")
        seen.append(body)
        choices = {"C2": "N", "C3": "Y", "C4": "U"} if len(seen) == 1 else {"C2": "N8", "C3": "NONE"}
        return {"usage": {"output_tokens": 0}, "answers": {key: {"choice": answer} for key, answer in choices.items()}}

    monkeypatch.setattr(provider, "_request", respond)
    picked = provider.columns("d1-2b", view)
    assert picked == {"C2": {"finding": "refuted", "evidence": ["C2", "N8"]},
                      "C3": {"finding": "supported", "evidence": []},
                      "C4": {"finding": "unknown", "evidence": []}}
    first, second = seen
    assert first["state"] == second["state"] and "已调单账户是逐笔交易本方账号" in second["state"]
    assert set(second["questions"]) == {"C2", "C3"}
    negative, positive = (second["questions"][key] for key in ("C2", "C3"))
    assert "表头第2行第2列 [C2]『本方保管账号』" in negative["instructions"]
    assert "另作他用，不能当作每笔流水实际收支的本方账号" in negative["instructions"]
    assert "表头第2行第3列 [C3]『已调单账户』" in positive["instructions"]
    assert "承载每笔流水实际收支的本方账号" in positive["instructions"]
    assert positive["criteria"]["N8"].startswith("[N8] 原件第1行独立说明：字段口径：已调单账户")
    assert positive["criteria"]["N9"].startswith("[N9] 原件第5行不完整的说明摘录")
    assert positive["criteria"]["N9"].endswith("…") and "NONE" in positive["criteria"]


@pytest.mark.parametrize("scenario", ["valid", "wrong_note", "abstention", "late_replacement", "wrong_recheck"])
def test_native_notes_for_a_still_unresolved_import_reach_the_public_gate(client, make_case, monkeypatch, scenario):
    book = Workbook()
    main = book.active
    main.title = "流水"
    main.append(["交易时间", "本方保管账号", "已调单账户", "本方暂记账号", "对方账号", "交易金额", "收付标志", "交易币种"])
    main.append(["2026-03-02 10:01:00", "SYN-KEEP", "SYN-REAL", "SYN-MEMO", "SYN-OTHER", "10.25", "出", "CNY"])
    main.append(["2026-03-02 10:02:00", "SYN-KEEP", "SYN-REAL", "SYN-MEMO", "SYN-OTHER", "11.50", "入", "CNY"])
    notes = book.create_sheet("字段说明")
    notes.append(["字段", "字段说明"])
    notes.append(["流水!B1 本方保管账号", "文件保管用途"])
    notes.append(["流水!C1 已调单账户", "逐笔交易本方账号"])
    notes.append(["流水!C2 已调单账户", "逐笔交易本方账号"])
    content = BytesIO()
    book.save(content)
    case = make_case()
    preview, layout, revision = setup_review(client, case, content.getvalue(), "routed-synthetic.xlsx")
    members = {row["sheet"]: row["member_path"] for row in preview["layout_review"]["members"]}
    data = {**revision, "member_path": members["流水"], "support_member_path": members["字段说明"],
            "header_row": 1, "field": "self_account"}
    original = client.post(layout + "/assistance", json=data)
    assert original.status_code == 200
    wrong_ref = next(row["id"] for row in original.json()["records"] if
                     row["locator"]["member_path"] == members["字段说明"] and row["locator"]["source_row"] == 4)
    source = imports._path(preview["import_id"], ".xlsx")
    endpoints = trial.parse_model_endpoints(ORIGINS)
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (endpoints,), raising=False)
    calls = []
    notes_by_column = {}

    def request(_provider, tag, route, body):
        assert route == "/v1/systemone"
        calls.append((tag, body))
        if "Y" in body["questions"]["C2"]["criteria"]:
            picks = {"C2": "N", "C3": "N" if scenario == "wrong_recheck" and tag == "d1-2b" else "Y", "C4": "U"}
        else:
            notes_by_column.update({column: next(note for note, description in body["questions"][column]["criteria"].items()
                                                 if title in description)
                                    for column, title in (("C2", "流水!B1 本方保管账号"), ("C3", "流水!C1 已调单账户"))})
            target = notes_by_column["C3"]
            assert "流水!C2 已调单账户" not in body["state"]
            assert wrong_ref not in body["questions"]["C3"]["criteria"]
            if scenario == "wrong_note":
                target = wrong_ref
            if scenario == "abstention" and tag == "d1-2b":
                target = "NONE"
            if scenario == "late_replacement" and tag == "d3-nano":
                source.write_bytes(b"X" * source.stat().st_size)
            picks = {"C2": notes_by_column["C2"], "C3": target}
        return {"usage": {"output_tokens": 0}, "answers": {key: {"choice": answer} for key, answer in picks.items()}}

    def recheck(_provider, view, prior, conflicts):
        assert scenario == "wrong_recheck"
        assert prior[0]["C3"] == {"finding": "refuted", "evidence": ["C3", notes_by_column["C3"]]}
        assert conflicts == ["C3"]
        return {"C2": {"finding": "refuted", "evidence": ["C2", notes_by_column["C2"]]},
                "C3": {"finding": "supported", "evidence": ["C3", notes_by_column["C2"]]},
                "C4": {"finding": "unknown", "evidence": []}}

    monkeypatch.setattr(trial._LoopbackProvider, "_request", request)
    monkeypatch.setattr(trial._LoopbackProvider, "reconsider_columns", recheck)
    result = client.post(layout + "/model-trial", json=data)
    assert [tag for tag, _ in calls] == ["d1-2b", "d1-2b", "d3-nano", "d3-nano"]
    if scenario == "late_replacement":
        assert result.status_code == 409 and result.json()["error"]["code"] == "source_changed"
        return
    assert result.status_code == 200, result.text
    payload = result.json()
    if scenario == "wrong_note":
        assert payload["state"] == "unavailable" and payload["reason"] == "provider_error"
        assert payload["packet"]["selection"] is None
        assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 0
        return
    assert payload["state"] == "pending" and payload["packet"]["selection"] is None
    assert payload["first_calls"] == 2 and payload["recheck_calls"] == (scenario == "wrong_recheck")
    check = next(check for check in payload["column_checks"] if check["column_ref"] == "C3")
    if scenario == "valid":
        assert (check["finding"], check["note_ref"]) == ("supported", notes_by_column["C3"])
    else:
        assert (check["finding"], check["note_ref"]) == ("unknown", None)
    assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 0


def test_qwen_column_reply_requires_a_current_applicable_original(monkeypatch):
    view = {
        "target": "每笔流水实际收支的本方账号", "candidates": ["C2", "C3"], "notes": ["N5", "N6"],
        "records": {
            "C2": {"表头": "甲账户", "原行": 1, "物理列": 2, "样本类型": ["digits"], "说明位置": ["N5"]},
            "C3": {"表头": "乙账户", "原行": 1, "物理列": 3, "样本类型": ["digits"], "说明位置": ["N6"]},
            "N5": {"说明": ["甲账户是逐笔交易本方账号"], "说明原行": 2, "完整": True},
            "N6": {"说明": ["乙账户是归集备用本方账号"], "说明原行": 3, "完整": True},
        },
    }
    provider = trial._LoopbackProvider(trial.parse_model_endpoints(ORIGINS))
    answer = {"C2": "supported:N5", "C3": "refuted:N6"}

    def respond(message, schema):
        assert "原行2" in message and "原行3" in message
        assert schema["properties"]["C2"]["enum"] == ["unknown", "supported:N5", "refuted:N5"]
        return answer

    monkeypatch.setattr(provider, "_completion", respond)
    assert provider.reconsider_columns(view, [], []) == {
        "C2": {"finding": "supported", "evidence": ["C2", "N5"]},
        "C3": {"finding": "refuted", "evidence": ["C3", "N6"]},
    }
    for code in ("supported", "supported:N6"):
        answer["C2"] = code
        with pytest.raises(trial.ProviderError, match="citation"):
            provider.reconsider_columns(view, [], [])
