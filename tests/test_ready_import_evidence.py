"""Public, source-bound pre-commit evidence in a temporary case."""
import hashlib
import io

from openpyxl import Workbook
import pytest

from app import db
from app.services import imports
from test_automatic_table_import import preview
from test_feedback_import_api import complete


UNKNOWN_ROWS = ("交易时间,本端账号,证件号,对方账号,交易金额,收付标志,交易币种\n"
                "2026-01-01 10:00:00,SYN-OWN,SYN-OWN,SYN-OTHER,100.00,出,CNY\n").encode()


def endpoint(cid, view):
    return f"/api/v1/cases/{cid}/imports/{view['import_id']}/ready-evidence"


def position(scope, column):
    return {"member_path": scope["member_path"], "header_row": scope["header_row"], "column": column}


def check(client, cid, view, field, claims):
    return client.post(endpoint(cid, view) + "/verify", json={
        "revision": view["ready_evidence"]["revision"], "field": field, "claims": claims,
    })


def propose(client, cid, view, action, columns):
    body = {"revision": view["ready_evidence"]["revision"], "field": "self_account", "action": action}
    if columns is not None:
        body["candidate_columns"] = columns
    return client.post(endpoint(cid, view) + "/proposal", json=body)


def test_proposal_accepts_only_current_physical_positions_for_review(client, make_case):
    cid, other = make_case("SYN proposal"), make_case("SYN other")
    view = preview(client, cid, UNKNOWN_ROWS)
    [scope] = view["ready_evidence"]["scopes"]
    account, document = position(scope, 2), position(scope, 3)
    both = [document, account]
    response = propose(client, cid, view, "CANDIDATE", both)
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "case_id": str(cid), "import_id": view["import_id"], "sha256": hashlib.sha256(UNKNOWN_ROWS).hexdigest(),
        "revision": view["ready_evidence"]["revision"], "parser_version": view["table_summary"]["profile"],
        "field": "self_account", "action": "CANDIDATE", "candidate_columns": both, "review_status": "review_only",
    }
    assert check(client, cid, view, "self_account", [account]).json()["evidence_status"] == "insufficient"
    assert check(client, cid, view, "self_account", [document]).json()["evidence_status"] == "refuted"

    for columns in ([], None, both):
        deferred = propose(client, cid, view, "DEFER", columns)
        assert deferred.status_code == 200, deferred.text
        assert deferred.json()["candidate_columns"] == (columns or [])
        assert deferred.json()["action"] == "DEFER"
    for columns in ([], None):
        assert propose(client, cid, view, "CANDIDATE", columns).status_code == 422
    for columns in ([account, account], [account, position(scope, 8)],
                    [account, {**document, "header_row": 2}], [{**account, "member_path": "outside.csv"}]):
        for action in ("CANDIDATE", "DEFER"):
            rejected = propose(client, cid, view, action, columns)
            assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "outside_ready_evidence"
    for column in (0, -1, "2", True, 2.0):
        assert propose(client, cid, view, "CANDIDATE", [position(scope, column)]).status_code == 422
    assert propose(client, cid, view, "ACCEPT", [account]).status_code == 422
    assert propose(client, other, view, "CANDIDATE", [account]).status_code == 404
    assert propose(client, other, view, "DEFER", []).status_code == 404
    assert not db.q_raw("select 1 from main_accounts where case_id=?", [cid])
    assert not db.q_raw("select 1 from account_ids where case_id=?", [cid])
    assert not db.q_raw("select 1 from flows where case_id=?", [cid])
    assert db.q_raw("select stage from import_sessions where import_id=?", [view["import_id"]]) == [{"stage": "ready"}]


def test_pending_source_read_uses_exact_preview_scope_and_never_imports(client, make_case):
    cid, other = make_case("SYN evidence"), make_case("SYN other")
    view = preview(client, cid, UNKNOWN_ROWS)
    access = view["ready_evidence"]
    [scope] = access["scopes"]
    assert scope["sample_rows"] == [2]
    data = {"revision": access["revision"], "member_path": scope["member_path"],
            "header_row": 1, "source_rows": [2], "columns": [2, 3]}
    response = client.post(endpoint(cid, view), json=data)
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["sha256"] == hashlib.sha256(UNKNOWN_ROWS).hexdigest()
    assert [c["value"] for c in body["headers"][0]["cells"]][1:3] == ["本端账号", "证件号"]
    assert [c["column"] for c in body["rows"][0]["cells"]] == [2, 3]
    assert [c["value"] for c in body["rows"][0]["cells"]] == ["SYN-OWN", "SYN-OWN"]
    assert client.post(endpoint(other, view), json=data).status_code == 404
    assert client.post(endpoint(cid, view), json={**data, "source_rows": [3]}).status_code == 422
    assert client.post(endpoint(cid, view), json={**data, "columns": [2, 20]}).status_code == 422
    assert check(client, cid, view, "self_account", [position(scope, 2)]).json()["evidence_status"] == "insufficient"
    invalid = check(client, cid, view, "self_account", [position(scope, 3)]).json()
    assert (invalid["evidence_status"], invalid["reason"]) == ("refuted", "document_number_column")
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0


def test_role_verification_requires_the_exact_column_in_each_sheet(client, make_case):
    cid = make_case("SYN two-sheet evidence")
    book = Workbook()
    left = book.active
    left.title = "甲"
    right = book.create_sheet("乙")
    left.append(["交易时间", "本方账号", "对方账号", "交易金额", "收付标志", "交易币种", "备注"])
    right.append(["交易时间", "备注", "本方账号", "对方账号", "交易金额", "收付标志", "交易币种"])
    left.append(["2026-01-01 10:00:00", "SYN-A", "SYN-P", "100.00", "出", "CNY", "SYN-A"])
    right.append(["2026-01-02 10:00:00", "SYN-B", "SYN-B", "SYN-P", "100.00", "出", "CNY"])
    buf = io.BytesIO()
    book.save(buf)
    view = preview(client, cid, buf.getvalue(), "synthetic.xlsx")
    access = view["ready_evidence"]
    assert access["all_regions_sampled"]
    scopes = {s["sheet"]: s for s in access["scopes"]}
    correct = [position(scopes["甲"], 2), position(scopes["乙"], 3)]
    assert check(client, cid, view, "self_account", correct).json()["evidence_status"] == "supported"
    assert check(client, cid, view, "self_account", correct[:1]).json()["evidence_status"] == "insufficient"
    same_value = [correct[0], position(scopes["乙"], 2)]
    assert check(client, cid, view, "self_account", same_value).json()["evidence_status"] == "refuted"
    assert check(client, cid, view, "self_account", [correct[0]] * 2).status_code == 422
    # Physical numbering is local to the sheet. A column number may recur.
    response = propose(client, cid, view, "CANDIDATE", same_value)
    assert response.status_code == 200 and response.json()["candidate_columns"] == same_value
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0


def test_evidence_generation_fences_repreview_and_commit(client, make_case):
    cid = make_case("SYN generation")
    content = UNKNOWN_ROWS.replace("本端账号".encode(), "本方账号".encode(), 1)
    view = preview(client, cid, content)
    revision = view["ready_evidence"]["revision"]
    body = {"mode": view["mode"], "mapping": view["mapping"], "ready_revision": revision}
    base = endpoint(cid, view).removesuffix("/ready-evidence")
    replay = client.post(base + "/preview", json=body)
    assert replay.status_code == 200, replay.text
    refreshed = replay.json()
    assert refreshed["ready_evidence"]["revision"] != revision
    for suffix in ("/preview", "/commit"):
        response = client.post(base + suffix, json=body)
        assert response.status_code == 409 and response.json()["error"]["code"] == "ready_evidence_stale"
    [scope] = view["ready_evidence"]["scopes"]
    response = check(client, cid, view, "self_account", [position(scope, 2)])
    assert response.status_code == 409
    assert propose(client, cid, view, "CANDIDATE", [position(scope, 2)]).status_code == 409
    assert propose(client, cid, view, "DEFER", []).status_code == 409
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0
    current = {**body, "ready_revision": refreshed["ready_evidence"]["revision"]}
    assert client.post(base + "/commit", json=current).status_code == 200
    assert complete(client, cid, refreshed)["result"]["ok"] == 1
    assert check(client, cid, refreshed, "self_account", [position(scope, 2)]).status_code == 409
    assert propose(client, cid, refreshed, "DEFER", []).status_code == 409
    retry = client.post(base + "/commit", json=current)
    assert retry.status_code == 200 and retry.json()["started"] is False


def test_manual_repreview_cannot_supply_its_own_identity_proof(client, make_case):
    cid = make_case("SYN manual provenance")
    initial = preview(client, cid, UNKNOWN_ROWS)
    path = endpoint(cid, initial)
    response = client.post(path.removesuffix("/ready-evidence") + "/preview", json={
        "mode": "B", "mapping": {**initial["mapping"], "self_account": "本端账号"},
        "ready_revision": initial["ready_evidence"]["revision"],
    })
    assert response.status_code == 200, response.text
    view = response.json()
    [scope] = view["ready_evidence"]["scopes"]
    assert view["table_summary"]["accepted"] == 1
    verdict = check(client, cid, view, "self_account", [position(scope, 2)])
    assert verdict.json()["evidence_status"] == "insufficient"
    assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0


def test_digest_check_and_late_generation_change_fail_closed(client, make_case, monkeypatch):
    from app import import_originals
    from app.api.models import RepreviewRequest

    cid = make_case("SYN late generation")
    view = preview(client, cid, UNKNOWN_ROWS)
    [scope] = view["ready_evidence"]["scopes"]
    data = {"revision": view["ready_evidence"]["revision"], "member_path": scope["member_path"],
            "header_row": 1, "source_rows": [2], "columns": [2]}
    original = import_originals.read_cells

    def during_read(*args, **kwargs):
        received = original(*args, **kwargs)
        body = RepreviewRequest(mode=view["mode"], mapping=view["mapping"])
        imports.repreview(cid, view["import_id"], body)
        return received

    with monkeypatch.context() as change:
        change.setattr(import_originals, "read_cells", during_read)
        stale = client.post(endpoint(cid, view), json=data)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "ready_evidence_stale"
    refreshed = preview(client, cid, UNKNOWN_ROWS)
    upload = imports._path(refreshed["import_id"], ".csv")
    edited = UNKNOWN_ROWS.replace(b"SYN-OWN", b"SYN-NEW", 1)
    assert len(edited) == len(UNKNOWN_ROWS)
    upload.write_bytes(edited)
    response = client.post(endpoint(cid, refreshed),
                           json={**data, "revision": refreshed["ready_evidence"]["revision"]})
    assert response.status_code == 409 and response.json()["error"]["code"] == "source_changed"


@pytest.mark.parametrize("action,include_column", [("CANDIDATE", True), ("DEFER", False)])
def test_proposal_rejects_edited_or_expired_source(client, make_case, action, include_column):
    cid = make_case("SYN proposal source")
    view = preview(client, cid, UNKNOWN_ROWS)
    [scope] = view["ready_evidence"]["scopes"]
    columns = [position(scope, 2)] if include_column else []
    upload = imports._path(view["import_id"], ".csv")
    edited = UNKNOWN_ROWS.replace(b"SYN-OWN", b"SYN-NEW", 1)
    assert len(edited) == len(UNKNOWN_ROWS)
    upload.write_bytes(edited)
    rejected = propose(client, cid, view, action, columns)
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "source_changed"
    upload.write_bytes(UNKNOWN_ROWS)
    db.x("update import_sessions set expires_at=? where import_id=?", ["2000-01-01T00:00:00Z", view["import_id"]])
    expired = propose(client, cid, view, action, columns)
    assert expired.status_code == 409 and expired.json()["error"]["code"] == "ready_evidence_unavailable"


def test_proposal_rechecks_generation_after_reading_original(client, make_case, monkeypatch):
    from app import import_originals
    from app.api.models import RepreviewRequest

    cid = make_case("SYN proposal concurrent preview")
    view = preview(client, cid, UNKNOWN_ROWS)
    [scope] = view["ready_evidence"]["scopes"]
    original = import_originals.read_cells

    def during_read(*args, **kwargs):
        cells = original(*args, **kwargs)
        imports.repreview(cid, view["import_id"], RepreviewRequest(mode=view["mode"], mapping=view["mapping"]))
        return cells

    monkeypatch.setattr(import_originals, "read_cells", during_read)
    response = propose(client, cid, view, "CANDIDATE", [position(scope, 2)])
    assert response.status_code == 409 and response.json()["error"]["code"] == "ready_evidence_stale"


def test_verified_tiered_header_and_orphan_are_distinguished(client, make_case):
    from test_xlsx_tiered_headers import DOWN, ORPHAN_ROWS, TWO_TIER_ROW, UP, workbook

    cid = make_case("SYN tier scope")
    view = preview(client, cid, workbook([UP, DOWN, TWO_TIER_ROW]), "two-tier.xlsx")
    [scope] = view["ready_evidence"]["scopes"]
    assert scope["header_rows"] == [1, 2]
    detail = client.post(endpoint(cid, view), json={
        "revision": view["ready_evidence"]["revision"], "member_path": scope["member_path"],
        "header_row": scope["header_row"], "source_rows": [3], "columns": [3],
    })
    assert detail.status_code == 200, detail.text
    assert [row["source_row"] for row in detail.json()["headers"]] == [1, 2]
    verdict = check(client, cid, view, "payer_account", [position(scope, 3)])
    assert verdict.json()["evidence_status"] == "supported", verdict.text

    mixed = preview(client, cid, workbook(ORPHAN_ROWS), "orphan.xlsx")
    access = mixed["ready_evidence"]
    assert not access["all_regions_sampled"]
    assert all(5 not in scope["sample_rows"] for scope in access["scopes"])
    proposals = [position(scope, 2) for scope in access["scopes"]]
    verdict = check(client, cid, mixed, "payer_account", proposals)
    assert verdict.json()["evidence_status"] == "insufficient"


def test_unseen_table_region_cannot_be_claimed_complete(client, make_case):
    from test_xlsx_tiered_headers import FLAT, workbook

    cid = make_case("SYN sample coverage")
    first = [FLAT, *[("2026-01-01", f"SYN-A{i}", "SYN-B", "10.00", "CNY") for i in range(10)]]
    second = [FLAT, ("2026-02-01", "SYN-C", "SYN-D", "20.00", "CNY")]
    view = preview(client, cid, workbook(first, extra=second), "coverage.xlsx")
    assert view["table_summary"]["accepted"] == 11
    assert len(view["ready_evidence"]["scopes"]) == 1
    assert not view["ready_evidence"]["all_regions_sampled"]
    [scope] = view["ready_evidence"]["scopes"]
    verdict = check(client, cid, view, "payer_account", [position(scope, 2)])
    assert (verdict.json()["evidence_status"], verdict.json()["reason"]) == ("insufficient", "incomplete_source_scope")


def test_known_empty_account_column_does_not_disprove_an_unrecognized_alias(client, make_case):
    cid = make_case("SYN account alias")
    data = ("交易时间,本方账号,入账载体号,对方账号,交易金额,收付标志,交易币种\n"
            "2026-01-01 10:00:00,,SYN-OWN,SYN-P,100.00,出,CNY\n").encode()
    view = preview(client, cid, data)
    [scope] = view["ready_evidence"]["scopes"]
    assert view["table_summary"]["accepted"] == 0
    proposed = check(client, cid, view, "self_account", [position(scope, 3)])
    assert (proposed.json()["evidence_status"], proposed.json()["reason"]) == ("insufficient", "source_role_unproven")


def test_competing_registered_aliases_cannot_be_promoted_by_equal_values(client, make_case):
    cid = make_case("SYN duplicate candidate role")
    data = ("交易时间,本方账号,交易账号,对方账号,交易金额,收付标志,交易币种\n"
            "2026-01-01 10:00:00,SYN-OWN,SYN-OWN,SYN-P,100.00,出,CNY\n").encode()
    view = preview(client, cid, data)
    [scope] = view["ready_evidence"]["scopes"]
    for column in (2, 3):
        verdict = check(client, cid, view, "self_account", [position(scope, column)])
        assert verdict.json()["evidence_status"] == "insufficient"
