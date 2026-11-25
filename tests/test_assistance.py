"""Case and source binding for read-only, positional model advice."""
import pytest

from app.services import imports
from test_bank_posting_pairs import encoded, entries
from test_import_layout_review import FIELDS, UNKNOWN, region, setup_review
from test_principal_book_model import book_row, import_books
from test_source_ledger import add_source
from test_trace_review import review


def ask_flow(client, case, anchor, context, kind, group=None):
    path = f"/api/v1/cases/{case}/review-assistance"
    body = {"kind": kind, "anchor_id": str(anchor), "review_token": context["review"]["token"]}
    if group is not None:
        body["group_flow_id"] = str(group)
    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert body["review_token"] not in response.text
    return path, body, response.json()


def submit(client, path, body, packet, choice, refs=()):
    return client.post(path, json={**body, "reply": {
        "packet_sha256": packet["packet_sha256"], "choice": choice, "refs": list(refs)}})


def test_assistance_refutes_identity_document_and_binds_layout(client, database, make_case):
    case = make_case("model read-only import")
    another_case = make_case("another import case")
    raw = UNKNOWN.replace("证件号".encode(), "备案证件号".encode()) + UNKNOWN.splitlines()[-1] + b"\n"
    view, path, revision = setup_review(client, case, raw)
    body = {**revision, "member_path": "table.csv", "header_row": 1, "field": "self_account"}
    route = path + "/assistance"
    initial = database.q_raw("select * from import_sessions where import_id=?", [view["import_id"]])
    response = client.post(route, json=body)
    assert response.status_code == 200, response.text
    packet = response.json()
    assert packet["facts"]["sampled_rows"] == [2, 3] and packet["facts"]["more_rows"]
    assert next(item for item in packet["candidates"] if item["id"] == "C7")["refuted"]
    denied = submit(client, route, body, packet, "C7", ["C7"])
    assert denied.status_code == 422 and denied.json()["error"]["code"] == "assistance_refuted"
    assert submit(client, route, body, packet, "C2", ["outside"]).status_code == 422
    allowed = submit(client, route, body, packet, "C2", ["C2"])
    assert allowed.json()["selection"] == {"status": "review_only", "choice": "C2", "refs": ["C2"], "gap": None}
    assert submit(client, route, body, packet, "DEFER").json()["selection"]["status"] == "deferred"
    assert client.post(route.replace(f"/cases/{case}/", f"/cases/{another_case}/"), json=body).status_code == 404
    assert submit(client, route, body, {**packet, "packet_sha256": "0" * 64}, "C2", ["C2"]).status_code == 409
    assert database.q_raw("select * from import_sessions where import_id=?", [view["import_id"]]) == initial
    denied_apply = client.post(path + "/apply", json={**revision, "confirmed": True,
        "regions": [region(last=4, fields={**FIELDS, "self_account": 7})]})
    assert denied_apply.status_code == 400
    assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 0


@pytest.mark.parametrize("heading", ["备注", "本方卡号"])
def test_assistance_and_product_role_verification_share_registered_refutation(client, make_case, heading):
    case = make_case()
    raw = UNKNOWN.replace("发生时点".encode(), "交易时间".encode()).replace("证件号".encode(), heading.encode())
    view, path, revision = setup_review(client, case, raw)
    body = {**revision, "member_path": "table.csv", "header_row": 1, "field": "self_account"}
    route = path + "/assistance"
    packet = client.post(route, json=body).json()
    assert next(item for item in packet["candidates"] if item["id"] == "C7")["refuted"]
    assert submit(client, route, body, packet, "C7", ["C7"]).status_code == 422
    verify = client.post(path.replace("layout-review", "ready-evidence") + "/verify", json={
        "revision": view["ready_evidence"]["revision"], "field": "self_account",
        "claims": [{"member_path": "table.csv", "header_row": 1, "column": 7}]})
    assert verify.status_code == 200 and verify.json()["evidence_status"] == "refuted"


@pytest.mark.parametrize("late", [False, True])
def test_pair_assistance_shows_time_gap_as_lead_and_keeps_confirmed_witnesses(client, make_case, late):
    case = make_case()
    values = entries()
    if late:
        values[3].update({"交易时间": "2038-02-04 09:00:04", "交易流水号": "SYN-PEER-OTHER-REFERENCE"})
    add_source(client, case, encoded(values))
    items = client.get(f"/api/v1/cases/{case}/flows", params={"granularity": "observations"}).json()["items"]
    anchor, peer = (next(row for row in items if row["raw_self_account"] == own and row["amount"] == "37.41")
                    for own in ("SYN-A", "SYN-B"))
    view = review(client, case, anchor["id"], x=10)
    path, body, packet = ask_flow(client, case, anchor["id"], view, "pair")
    choice = f"F{peer['id']}"
    assert choice in {item["id"] for item in packet["candidates"]}
    result = submit(client, path, body, packet, choice, [choice])
    assert result.status_code == 200 and result.json()["selection"]["status"] == "review_only"
    if late:
        assert peer["id"] not in view["review"]["pair"]["candidate_ids"]
        assert packet["facts"]["rule_status"] != "confirmed" and packet["facts"]["needs_model"]
    else:
        assert packet["facts"]["rule_status"] == "confirmed" and not packet["facts"]["needs_model"]
        assert {item["locator"]["source_row"] for item in packet["records"] if item["id"].startswith("W")} == {2, 4}
    assert client.get(f"/api/v1/cases/{case}/flows", params={"granularity": "observations"}).json()["total"] == len(items)


def test_trace_advice_cites_untracked_rows_and_preserves_product_interval(client, make_case):
    case = make_case()
    rows = [book_row("B", "A", "B", "100.00", "140.00", 0, 1, "SOURCE"),
            book_row("B", "B", "D", "50.00", "90.00", 1, 2, "COMPETITOR"),
            book_row("B", "B", "C", "90.00", "0.00", 2, 3, "TARGET")]
    found = import_books(client, case, rows)
    source, competitor, target = (found[f"SYN-B-{ref}"] for ref in ("SOURCE", "COMPETITOR", "TARGET"))
    context = review(client, case, target["id"], x=3)
    path, body, packet = ask_flow(client, case, target["id"], context, "trace_source", source["id"])
    assert packet["facts"]["conditional_group_share"] == {"min": "50.00", "max": "90.00"}
    assert packet["facts"]["attribution_status"] == "unknown"
    key = f"F{competitor['id']}"
    chosen = next(row for row in packet["records"] if row["id"] == key)
    assert chosen["locator"]["source_row"] == 3
    assert client.get(chosen["locator"]["record_url"]).json()["items"][0]["source_row"] == 3
    assert submit(client, path, body, packet, key, [key]).json()["selection"]["choice"] == key
    assert submit(client, path, body, packet, f"F{source['id']}", [f"F{source['id']}"]).status_code == 422
    assert client.delete("/api/context/review", params={"case_id": case},
                         headers={"X-FundsTrace-Review": body["review_token"]}).status_code in (200, 204)
    assert submit(client, path, body, packet, key, [key]).status_code == 409
