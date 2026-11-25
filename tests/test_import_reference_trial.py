"""Public reference advice, immutable original columns, and formal readback."""
import csv
from dataclasses import replace
from hashlib import sha256
import io
import json
from types import SimpleNamespace

import pytest
from openpyxl import Workbook, load_workbook

from app import table_source_roles
from app.services import desktop_model_trial as model, import_reference_trial, import_verbatim_balance, imports
from test_api_v1 import poll
from test_automatic_table_import import preview
from test_feedback31_reference import payload
from test_uniform35_reference import row, workbook


_HEADER = "交易时间,本方账号,对方账号,交易金额,收付标志,交易币种,凭证号,交易流水号,隐私表头SYN-SENSITIVE"
_DATA = ("2040-03-01 08:10:00,000123,000234,12.34,入,CNY,SYN-VOUCHER-1,SYN-TRANSACTION-1,\n"
         "2040-03-01 08:20:00,000123,000235,18.25,出,CNY,SYN-VOUCHER-2,SYN-TRANSACTION-2,\n"
         "2040-03-01 08:30:00,000123,000236,11.09,入,CNY,SYN-VOUCHER-3,,\n")
_PAYLOAD = (_HEADER + "\n" + _DATA).encode()
_RIGHT = {"choice": "SEPARATE", "transaction": "C8", "voucher": "C7"}
_DEFER = {"choice": "DEFER", "transaction": "NONE", "voucher": "NONE"}
_WRONG = {"choice": "SEPARATE", "transaction": "C7", "voucher": "C8"}


def original(client, cid, raw=_PAYLOAD, name="synthetic.csv"):
    value = preview(client, cid, raw, name)
    base = f"/api/v1/cases/{cid}"
    route = f"{base}/imports/{value['import_id']}"
    access = value["layout_review"]
    body = {"revision": access["revision"], "member_path": access["members"][0]["member_path"], "header_row": 1}
    return base, route, value, body


def trial(client, route, body):
    return client.post(route + "/layout-review/reference-policy-trial", json=body)


def test_registered_workbook_corrects_voucher_fallback_without_new_financial_rows(
        client, database, make_case, monkeypatch):
    headings = table_source_roles._TRANSACTION_EXPLICIT
    entries = [row(), row(**{"交易流水号": "", "凭证号": "SYN-ONLY-VOUCHER", "交易时间": "2041-01-02 01:02:03"})]
    book = load_workbook(io.BytesIO(payload(entries, headings, "xlsx")))
    for number in (2, 3):
        book.active.cell(number, len(headings) + 3).number_format = "@"
    saved = io.BytesIO()
    book.save(saved)
    book.close()
    raw = saved.getvalue()
    monkeypatch.setattr(client.app.state, "desktop_model_trial", None, raising=False)
    case = make_case("Pinned ordinary workbook")
    old = preview(client, case, raw, "synthetic.xlsx")
    assert old["automatic"] and old["stats"]["ok"] == 2
    assert not old["reference_policy_basis"]
    assert {flow["trade_no"] for flow in old["preview"]} == {"SYN-PRIMARY", "SYN-ONLY-VOUCHER"}

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,))
    monkeypatch.setattr(model, "_LoopbackProvider", lambda *_: pytest.fail("A literal source rule needs no model"))
    case = make_case("Scoped trial workbook")
    base, job, fixed, body = original(client, case, raw, "synthetic.xlsx")
    assert fixed["reference_policy_basis"] == "rule_verified_source_columns"
    assert not fixed["automatic"] and fixed["stats"]["ok"] == 2
    assert {flow["trade_no"] for flow in fixed["preview"]} == {"SYN-PRIMARY", ""}
    assert client.post(job + "/commit", json={
        "mode": fixed["mode"], "mapping": fixed["mapping"], "ready_revision": body["revision"],
    }).raise_for_status().json()["started"]
    receipt = poll(client, case, fixed["import_id"])["result"]
    assert receipt["ok"] == 2
    with database._lock:
        database._con.close()
        database._con = None
    flows = client.get(base + "/flows").raise_for_status().json()
    assert {flow["trade_no"] for flow in flows["items"]} == {"SYN-PRIMARY", ""}
    assert flows["funds"]["confirmed_event_count"] == 0
    [journal] = client.get(job + "/records", params={"source_row": 3}).raise_for_status().json()["items"]
    assert journal["details"]["trade_no_basis"] == "none"
    references = {ref["role"]: ref["raw"] for ref in json.loads(journal["details"]["source_field_evidence"])}
    assert references["source_transaction_id"] == "" and references["independent_voucher_reference"] == "SYN-ONLY-VOUCHER"
    src = base + "/sources/" + fixed["import_id"]
    assert client.delete(src + "/operation", params={"expected_batch_id": receipt["batch_id"]}).status_code == 200
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0
    assert client.get(src + "/file").content == raw


@pytest.mark.parametrize("long_voucher", [False, True])
def test_distinct_vouchers_stay_separate_when_verified_primary_is_blank(
        client, database, make_case, monkeypatch, long_voucher):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Scoped voucher occurrences")
    left = row(**{"交易流水号": "", "凭证号": "SYN-A-VOUCHER" + ("A" * 2200 if long_voucher else "")})
    right = {**left, "凭证号": "SYN-B-VOUCHER"}
    raw = payload([left, right, left], table_source_roles._TRANSACTION_EXPLICIT, "xlsx")

    def confirm():
        base, route, ready, body = original(client, cid, raw, "synthetic.xlsx")
        assert ready["reference_policy_basis"] == "rule_verified_source_columns"
        assert ready["stats"]["ok"] == 3 and {entry["trade_no"] for entry in ready["preview"]} == {""}
        assert client.post(route + "/commit", json={"mode": ready["mode"], "mapping": ready["mapping"],
            "ready_revision": body["revision"]}).raise_for_status().json()["started"]
        rec = poll(client, cid, ready["import_id"])["result"]
        records = client.get(route + "/records").raise_for_status().json()["items"]
        ids = [record["flow_id"] for record in records]
        assert ids[0] == ids[2] and ids[0] != ids[1] and None not in ids
        assert bool(records[0]["details"].get("source_field_evidence_limited")) is long_voucher
        return base, rec, ids

    base, first, ids = confirm()
    assert (first["ok"], first["rows_dup"]) == (2, 1)
    with database._lock:
        database._con.close()
        database._con = None
    _, second, duplicates = confirm()
    assert duplicates == ids and (second["ok"], second["rows_dup"]) == (0, 3)
    page = client.get(base + "/flows").raise_for_status().json()
    assert len(page["items"]) == 2 and page["funds"]["confirmed_event_count"] == 0
    assert {entry["trade_no"] for entry in page["items"]} == {""}
    package = client.get(f"/api/cases/{cid}/export").raise_for_status().content
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package)})
    restored_case = restored.raise_for_status().json()["case_id"]
    assert client.get(f"/api/v1/cases/{restored_case}/flows").raise_for_status().json()["total"] == 2
    for index, receipt in enumerate((first, second)):
        source = f"{base}/sources/{receipt['source_id']}"
        assert client.delete(source + "/operation",
                             params={"expected_batch_id": receipt["batch_id"]}).status_code == 200
        assert client.get(source + "/file").content == raw
        assert client.get(base + "/flows").raise_for_status().json()["total"] == (2, 0)[index]


@pytest.fixture
def frozen_model_upload(monkeypatch):
    """Retain a ready preview created before first-upload rule selection."""
    monkeypatch.setattr(import_reference_trial, "prepare_upload", lambda value: value)


def engine(monkeypatch, client, answers):
    replies = iter(answers)
    called = []

    class Provider:
        def __init__(self, endpoints):
            assert endpoints is not None

        def reference_policy(self, tag, view, questions, prior=None):
            serialized = json.dumps(view)
            assert "SYN-" not in serialized and "000123" not in serialized
            assert len(view["header_columns"]) == 9
            assert view["covered_rows"] == 3
            assert view["comparisons"] == [{"columns": ["C7", "C8"], "both_nonempty": 2,
                                           "equal_when_both_nonempty": 0, "first_difference": 2}]
            called.append((tag, prior))
            return next(replies)

    monkeypatch.setattr(client.app.state, "desktop_model_trial", ({},), raising=False)
    monkeypatch.setattr(model, "_LoopbackProvider", Provider)
    return called


@pytest.mark.parametrize("recheck", [False, True])
def test_verified_number_suggestion_prepares_manual_commit_and_never_fills_blank_primary(
        client, database, make_case, monkeypatch, frozen_model_upload, recheck):
    answers = [_RIGHT, _DEFER, _RIGHT] if recheck else [_RIGHT, _RIGHT]
    called = engine(monkeypatch, client, answers)
    cid = make_case()
    base, route, default, body = original(client, cid)
    assert default["reference_policy_available"] and default["reference_policy"] is None
    assert not default["automatic"] and default["stats"]["accepted"] == 1
    result = trial(client, route, body).raise_for_status().json()
    assert (result["state"], result["first_calls"], result["recheck_calls"]) == ("proposal", 2, int(recheck))
    assert [tag for tag, _ in called] == [*model._FIRST, *([model._RECHECK] if recheck else [])]
    assert result["packet"]["selection"]["refs"] == ["C8", "C7"]
    prepared = result["prepared_preview"]
    assert prepared["stats"]["accepted"] == 3
    assert prepared["reference_policy_basis"] == "model_verified_source_columns"
    assert prepared["layout_model_assisted"] and not prepared["automatic"]
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0
    assert trial(client, route, body).status_code == 409

    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    ready = client.post(route + "/preview", json={
        "mode": prepared["mode"], "mapping": prepared["mapping"],
    }).raise_for_status().json()
    assert ready["reference_policy_basis"] == prepared["reference_policy_basis"]
    body = {"mode": ready["mode"], "mapping": ready["mapping"], "mark_accounts": []}
    assert client.post(route + "/commit", json=body).status_code == 409
    committed = client.post(route + "/commit", json={**body, "ready_revision": ready["layout_review"]["revision"]})
    assert committed.status_code == 200
    job = poll(client, cid, ready["import_id"])
    assert job["stage"] == "done" and job["result"]["ok"] == 3
    flows = client.get(base + "/flows").raise_for_status().json()
    assert {flow["trade_no"] for flow in flows["items"]} == {"SYN-TRANSACTION-1", "SYN-TRANSACTION-2", ""}
    assert flows["funds"]["confirmed_event_count"] == 0
    [journal] = client.get(route + "/records", params={"source_row": 4}).raise_for_status().json()["items"]
    refs = {record["role"]: record for record in json.loads(journal["details"]["source_field_evidence"])}
    assert journal["details"]["trade_no_basis"] == "none"
    assert journal["details"]["source_reference_policy"] == "verified-reference-columns-v1"
    assert refs["source_transaction_id"]["raw"] == "" and refs["source_transaction_id"]["column_index"] == 8
    assert (refs["independent_voucher_reference"]["raw"], refs["independent_voucher_reference"]["column_index"]) == (
        "SYN-VOUCHER-3", 7)
    source_id = ready["import_id"]
    assert client.get(base + f"/sources/{source_id}/file").content == _PAYLOAD
    assert client.delete(base + f"/sources/{source_id}/operation",
                         params={"expected_batch_id": job["result"]["batch_id"]}).status_code == 200
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


@pytest.mark.parametrize("bad", [_WRONG, {**_RIGHT, "voucher": "C6"}])
def test_wrong_order_or_unrelated_citations_do_not_change_the_preview(client, make_case, monkeypatch, frozen_model_upload, bad):
    engine(monkeypatch, client, [bad, bad])
    base, route, _, body = original(client, make_case())
    result = trial(client, route, body).raise_for_status().json()
    assert result["state"] in {"pending", "unavailable"} and result["prepared_preview"] is None
    assert result["recheck_calls"] == 0
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


def test_scope_duplicate_or_manual_mapping_ends_before_dispatch(client, make_case, monkeypatch, frozen_model_upload):
    called = engine(monkeypatch, client, [])
    repeated = (_HEADER + "\n" + _DATA + _HEADER + "\n" + _DATA).encode()
    _, route, _, body = original(client, make_case("two scopes"), repeated)
    assert trial(client, route, body).raise_for_status().json()["reason"] == "reference_scope_incomplete"
    duplicate = (_HEADER.replace("隐私表头SYN-SENSITIVE", "凭证编号") + "\n" + _DATA).encode()
    _, route, _, body = original(client, make_case("competing role"), duplicate)
    assert trial(client, route, body).raise_for_status().json()["reason"] == "reference_columns_unproved"
    _, route, view, body = original(client, make_case("explicit override"))
    override = client.post(route + "/preview", json={
        "mode": view["mode"], "mapping": {**view["mapping"], "channel": "隐私表头SYN-SENSITIVE"},
    }).raise_for_status().json()
    body["revision"] = override["layout_review"]["revision"]
    assert trial(client, route, body).raise_for_status().json()["reason"] == "reference_policy_manual_override"
    _, route, view, body = original(client, make_case("exact35"), workbook([row()]), "synthetic.xlsx")
    assert view["reference_policy_basis"] == "uniform35_source_header"
    assert trial(client, route, body).raise_for_status().json()["reason"] == "reference_policy_already_selected"
    assert called == []


def test_ready_digest_and_view_are_rechecked_after_native_response(client, make_case, monkeypatch, frozen_model_upload):
    cid = make_case()
    monkeypatch.setattr(client.app.state, "desktop_model_trial", ({},), raising=False)
    _, route, view, body = original(client, cid)
    path = imports._path(view["import_id"], ".csv")

    class Provider:
        def __init__(self, endpoints):
            pass

        def reference_policy(self, tag, view, questions, prior=None):
            if tag == model._FIRST[-1]:
                path.write_bytes(_PAYLOAD.replace(b"SYN-VOUCHER-1", b"SYN-VOUCHER-X"))
            return _RIGHT

    monkeypatch.setattr(model, "_LoopbackProvider", Provider)
    denied = trial(client, route, body)
    assert (denied.status_code, denied.json()["error"]["code"]) == (409, "source_changed")


def test_regular_explicit_reference_option_keeps_its_frozen_fallback(client, make_case, monkeypatch, frozen_model_upload):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    _, route, view, body = original(client, make_case())
    closed = trial(client, route, body).raise_for_status().json()
    assert closed["state"] == "unavailable" and closed["first_calls"] == 0
    explicit = client.post(route + "/preview", json={
        "mode": view["mode"], "mapping": view["mapping"], "reference_policy": "transaction-reference-v1",
    }).raise_for_status().json()
    assert explicit["stats"]["accepted"] == 3 and explicit["reference_policy_basis"] is None
    assert explicit["preview"][-1]["trade_no"] == "SYN-VOUCHER-3"


def test_same_original_same_verified_policy_deduplicates_and_mixed_historical_policies_reject(
        client, make_case, monkeypatch, frozen_model_upload):
    engine(monkeypatch, client, [_RIGHT] * 4)

    def submit(case, route, preview_value):
        commit = client.post(route + "/commit", json={
            "mode": preview_value["mode"], "mapping": preview_value["mapping"], "mark_accounts": [],
            "ready_revision": preview_value["layout_review"]["revision"],
        })
        if commit.status_code != 200:
            return commit
        return poll(client, case, preview_value["import_id"])

    prior = make_case("Old reference interpretation")
    base, route, first, _ = original(client, prior)
    assert submit(prior, route, first)["result"]["ok"] == 1
    _, repeated, _, body = original(client, prior)
    conflict = trial(client, repeated, body)
    assert (conflict.status_code, conflict.json()["error"]["code"]) == (409, "reference_original_active")
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 1

    current = make_case("Verified reference interpretation")
    base, route, _, body = original(client, current)
    first = trial(client, route, body).raise_for_status().json()["prepared_preview"]
    assert submit(current, route, first)["result"]["ok"] == 3
    _, second_route, plain, second_scope = original(client, current)
    conflict = submit(current, second_route, plain)
    assert (conflict.status_code, conflict.json()["error"]["code"]) == (409, "reference_original_active")
    second = trial(client, second_route, second_scope).raise_for_status().json()["prepared_preview"]
    assert second["stats"]["accepted"] == 3
    second_job = submit(current, second_route, second)
    assert second_job["stage"] == "done" and second_job["result"]["rows_dup"] == 3
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 3
    for item, path in ((first, route), (second, second_route)):
        assert client.get(base + f"/sources/{item['import_id']}/file").content == _PAYLOAD
        rows = client.get(path + "/review", params={"member_path": "table.csv"}).raise_for_status().json()["items"]
        assert len(rows) == 3 and all(row["flow_id"] and row["original"] for row in rows)


def test_one_provider_error_does_not_skip_other_first_pass(client, make_case, monkeypatch, frozen_model_upload):
    called = []

    class Provider:
        def __init__(self, endpoints):
            pass

        def reference_policy(self, tag, view, questions, prior=None):
            called.append(tag)
            if tag == model._FIRST[0]:
                raise model.ProviderError("Synthetic transport failure")
            return _RIGHT

    monkeypatch.setattr(client.app.state, "desktop_model_trial", ({},), raising=False)
    monkeypatch.setattr(model, "_LoopbackProvider", Provider)
    _, route, _, body = original(client, make_case())
    result = trial(client, route, body).raise_for_status().json()
    assert (result["state"], result["reason"], result["first_calls"], result["recheck_calls"]) == (
        "unavailable", "provider_error", 2, 0)
    assert called == list(model._FIRST)


def test_restored_verified_reference_source_blocks_reimport_without_its_live_policy_binding(
        client, make_case, monkeypatch, frozen_model_upload, tmp_path):
    from test_cases import archive_of, import_package, members, rewrite_parquet

    called = engine(monkeypatch, client, [_RIGHT, _RIGHT])
    case = make_case("Verified source before case package")
    _, route, _, scope = original(client, case)
    decided = trial(client, route, scope).raise_for_status().json()["prepared_preview"]
    assert client.post(route + "/commit", json={
        "mode": decided["mode"], "mapping": decided["mapping"], "mark_accounts": [],
        "ready_revision": decided["layout_review"]["revision"],
    }).status_code == 200
    assert poll(client, case, decided["import_id"])["result"]["ok"] == 3
    package = client.get(f"/api/cases/{case}/export").raise_for_status().content
    forged = members(package)
    rewrite_parquet(forged, "import_records", """update rows set details_json=json_merge_patch(
        details_json, '{"trade_no_basis":"legacy_voucher_fallback"}') where source_row=4""", tmp_path)
    assert import_package(client, archive_of(forged)).status_code == 400
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package)})
    assert restored.status_code == 200, restored.text
    new_case = restored.json()["case_id"]
    base, route, plain, scope = original(client, new_case)
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 3
    refused = client.post(route + "/commit", json={
        "mode": plain["mode"], "mapping": plain["mapping"], "mark_accounts": [],
    })
    assert (refused.status_code, refused.json().get("error", {}).get("code")) == (409, "reference_original_active")
    advised = trial(client, route, scope)
    assert (advised.status_code, advised.json().get("error", {}).get("code")) == (409, "reference_original_active")
    assert len(called) == 2
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 3


def test_first_trial_upload_preselects_verified_source_numbers_and_requires_confirmation(
        client, database, make_case, monkeypatch):
    called = engine(monkeypatch, client, [])
    cid = make_case("Source number rule")
    base, route, selected, scope = original(client, cid)
    assert selected["reference_policy"] == "transaction-reference-v1"
    assert selected["reference_policy_basis"] == "rule_verified_source_columns"
    assert selected["stats"]["accepted"] == 3
    assert selected["automatic"] is False and selected["layout_model_assisted"] is False
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0
    assert called == []
    confirm = {"mode": selected["mode"], "mapping": selected["mapping"], "mark_accounts": []}
    assert client.post(route + "/commit", json=confirm).status_code == 409
    assert client.post(route + "/commit", json={**confirm, "ready_revision": scope["revision"]}).status_code == 200
    job = poll(client, cid, selected["import_id"])
    assert job["stage"] == "done" and job["result"]["ok"] == 3
    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    loaded = client.get(base + "/flows").raise_for_status().json()
    assert {flow["trade_no"] for flow in loaded["items"]} == {"SYN-TRANSACTION-1", "SYN-TRANSACTION-2", ""}
    assert loaded["funds"]["confirmed_event_count"] == 0
    [record] = client.get(route + "/records", params={"source_row": 4}).raise_for_status().json()["items"]
    fields = {item["role"]: item for item in json.loads(record["details"]["source_field_evidence"])}
    assert fields["independent_voucher_reference"]["raw"] == "SYN-VOUCHER-3"
    assert fields["source_transaction_id"]["raw"] == ""
    assert client.delete(base + f"/sources/{selected['import_id']}/operation",
                         params={"expected_batch_id": job["result"]["batch_id"]}).status_code == 200
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


def test_first_upload_rule_respects_clear_and_leaves_old_ready_sessions_unselected(client, make_case, monkeypatch):
    _, old_route, old, _ = original(client, make_case("Nontrial ready"))
    assert old["reference_policy_basis"] is None and old["stats"]["accepted"] == 1
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    refreshed_old = client.post(old_route + "/preview", json={
        "mode": old["mode"], "mapping": old["mapping"],
    }).raise_for_status().json()
    assert refreshed_old["reference_policy"] is None and refreshed_old["stats"]["accepted"] == 1
    _, route, selected, _ = original(client, make_case("Clear prepared source number choice"))
    assert selected["reference_policy_basis"] == "rule_verified_source_columns"
    cleared = client.post(route + "/preview", json={
        "mode": selected["mode"], "mapping": selected["mapping"], "reference_policy": None,
    }).raise_for_status().json()
    assert cleared["reference_policy"] is None and cleared["reference_policy_basis"] is None
    assert cleared["stats"]["accepted"] == 1
    reopened = client.post(route + "/preview", json={
        "mode": cleared["mode"], "mapping": cleared["mapping"],
    }).raise_for_status().json()
    assert reopened["reference_policy"] is None and reopened["stats"]["accepted"] == 1


def test_source_number_rule_keeps_existing_preview_when_an_accepted_fact_changes(client, make_case, monkeypatch):
    old_parse = imports._parse

    def changed(*args, **kwargs):
        parsed = old_parse(*args, **kwargs)
        if kwargs.get("verified_reference") is not None:
            from decimal import Decimal
            parsed[4][-1]["amount"] = Decimal("100.00")
        return parsed

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    monkeypatch.setattr(imports, "_parse", changed)
    base, _, selected, _ = original(client, make_case("Preserve original ledger facts"))
    assert selected["reference_policy"] is None and selected["reference_policy_basis"] is None
    assert selected["stats"]["accepted"] == 1 and selected["preview"][0]["amount"] == "11.09"
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


@pytest.mark.parametrize("data", [
    (_HEADER + "\n" + _DATA + _HEADER + "\n" + _DATA).encode(),
    (_HEADER.replace("隐私表头SYN-SENSITIVE", "凭证编号") + "\n" + _DATA).encode(),
    (_HEADER + "\n" + _DATA.replace("SYN-VOUCHER-1", "SYN-TRANSACTION-1")
     .replace("SYN-VOUCHER-2", "SYN-TRANSACTION-2")).encode(),
])
def test_first_upload_leaves_multi_region_competing_or_equal_reference_roles_unselected(
        client, make_case, monkeypatch, data):
    called = engine(monkeypatch, client, [])
    base, _, value, _ = original(client, make_case(), data)
    assert value["reference_policy"] is value["reference_policy_basis"] is None
    assert called == [] and client.get(base + "/flows").raise_for_status().json()["total"] == 0


def reference_workbook(*, malformed=None):
    headers = ("交易时间", "本方账号", "对方账号", "交易金额", "收付标志", "交易币种",
               "凭证号", "交易流水号", "日志号", "备注")
    if malformed == "second_voucher":
        headers = (*headers[:-1], "凭证编号")
    records = [
        ("2040-01-14 08:10:00", "SYN-BOOK", "SYN-PEER-A", "25.12", "入", "CNY", "SYN-V1", "SYN-T1", "0", "SYN note"),
        ("2040-01-14 08:22:00", "SYN-BOOK", "SYN-PEER-B", "18.31", "出", "CNY", "SYN-V2", "SYN-T2", "", "SYN note"),
        ("2040-01-14 08:54:00", "SYN-BOOK", "SYN-PEER-C", "10.02", "入", "CNY", "SYN-V3", "SYN-T3"),
    ]
    if malformed == "required_missing":
        records[-1] = records[-1][:-1]
    if malformed == "extends_past_header":
        records[-1] += ("", "", "SYN outside header")
    if malformed == "repeated_header":
        records.extend((headers, records[0]))
    book = Workbook()
    sheet = book.active
    for values in (headers, *records):
        sheet.append(values)
    if malformed == "second_sheet":
        book.copy_worksheet(sheet)
    target = io.BytesIO()
    book.save(target)
    book.close()
    return target.getvalue()


@pytest.fixture
def local_reference(client, database, make_case, monkeypatch, tmp_path):
    isolated = tmp_path / "funds-trace-model-trial-references"
    isolated.mkdir(mode=0o700)
    (isolated / "data").mkdir()
    monkeypatch.setattr(database, "DATA_DIR", isolated / "data")
    monkeypatch.setattr(import_verbatim_balance, "sys", SimpleNamespace(platform="darwin"))
    endpoints = model.parse_model_endpoints({
        tag: f"http://127.0.0.1:{65001 + index}" for index, tag in enumerate((*model._FIRST, model._RECHECK))
    })
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (endpoints,), raising=False)
    monkeypatch.setattr(client.app.state, "desktop_verbatim_reference_review", None, raising=False)

    def ready(raw):
        cid = make_case()
        base, route, preview, body = original(client, cid, raw, "references.xlsx")
        assert preview["reference_policy_available"] and preview["reference_policy"] is None
        assert preview["stats"]["unresolved"] == 3
        grant = import_reference_trial.LocalReferenceReview(
            int(cid), preview["import_id"], body["revision"], sha256(raw).hexdigest(), body["member_path"], (2, 3, 4))
        client.app.state.desktop_verbatim_reference_review = grant
        return cid, base, route, preview, body, grant

    return ready


def test_authorized_local_reference_sends_the_same_original_cells_to_both_heads_and_recheck(
        client, database, monkeypatch, local_reference):
    raw = reference_workbook()
    cid, base, route, preview, body, _ = local_reference(raw)
    requests = []

    def transport(_, tag, endpoint, payload):
        requests.append((tag, endpoint, model._json(payload)))
        if endpoint == "/v1/systemone":
            answer = _RIGHT if tag == model._FIRST[0] else _DEFER
            return {"answers": {key: {"choice": value} for key, value in answer.items()},
                    "usage": {"output_tokens": 0}}
        if endpoint == "/apply-template":
            assert payload["chat_template_kwargs"] == {"enable_thinking": False}
            return {"prompt": "<|im_start|>user\n" + payload["messages"][0]["content"] + "<|im_end|>\n" + model._NO_THINK}
        if endpoint == "/tokenize":
            return {"tokens": [1]}
        assert endpoint == "/completion"
        return {"content": json.dumps(_RIGHT), "truncated": False, "stop_type": "eos"}

    monkeypatch.setattr(model._LoopbackProvider, "_request", transport)
    result = trial(client, route, body).raise_for_status().json()
    assert result["state"] == "proposal" and result["reason"] == "recheck_supported"
    assert (result["first_calls"], result["recheck_calls"]) == (2, 1)
    assert [entry[:2] for entry in requests] == [
        (model._FIRST[0], "/v1/systemone"), (model._FIRST[1], "/v1/systemone"),
        (model._RECHECK, "/apply-template"), (model._RECHECK, "/tokenize"), (model._RECHECK, "/completion")]
    assert requests[0][2] == requests[1][2]
    sent = json.loads(requests[0][2])
    view = json.loads(sent["state"][sent["state"].index("{"):])
    assert view["member_path"] == body["member_path"] and view["header_row"] == 1
    assert view["原行"] == {
        "R2": {"C7": {"kind": "text", "value": "SYN-V1"}, "C8": {"kind": "text", "value": "SYN-T1"},
               "C9": {"kind": "text", "value": "0"}},
        "R3": {"C7": {"kind": "text", "value": "SYN-V2"}, "C8": {"kind": "text", "value": "SYN-T2"},
               "C9": {"kind": "text", "value": ""}},
        "R4": {"C7": {"kind": "text", "value": "SYN-V3"}, "C8": {"kind": "text", "value": "SYN-T3"}, "C9": None},
    }
    for role in ("transaction", "voucher"):
        assert sent["questions"][role]["criteria"] == {
            "C7": "C7｜凭证号", "C8": "C8｜交易流水号", "C9": "C9｜日志号", "NONE": "没有足够原列证据"}
    qwen_body = json.loads(requests[2][2])
    text = qwen_body["messages"][0]["content"]
    review = json.loads(text[text.index("{"):])
    assert review == {"原表": view, "首判": [_RIGHT, _DEFER], "问题": sent["questions"]}
    assert text in json.loads(requests[3][2])["content"]
    assert "原行" not in result["packet"]["facts"]
    assert "SYN-" not in json.dumps(result["packet"])
    [session] = database.q_raw("select request_json from import_sessions where import_id=?", [preview["import_id"]])
    assert "SYN-V" not in session["request_json"]
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0

    ready = result["prepared_preview"]
    commit = client.post(route + "/commit", json={"mode": ready["mode"], "mapping": ready["mapping"],
                          "ready_revision": ready["layout_review"]["revision"]}).raise_for_status().json()
    assert commit["started"]
    receipt = poll(client, cid, ready["import_id"])["result"]
    assert receipt["ok"] == 3
    with database._lock:
        database._con.close()
        database._con = None
    flows = client.get(base + "/flows").raise_for_status().json()["items"]
    assert {row["trade_no"] for row in flows} == {"SYN-T1", "SYN-T2", "SYN-T3"}
    source = base + "/sources/" + ready["import_id"]
    assert client.delete(source + "/operation", params={"expected_batch_id": receipt["batch_id"]}).status_code == 200
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0
    assert client.get(source + "/file").content == raw


def test_local_reference_distinguishes_leading_zero_text_number_and_an_interior_absent_cell(
        client, monkeypatch, local_reference):
    book = load_workbook(io.BytesIO(reference_workbook()))
    sheet = book.active
    sheet["G2"] = "000014"
    sheet["H2"] = 14
    sheet["I3"] = ""
    sheet["J4"] = "SYN physical tail"
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    _, _, route, _, body, _ = local_reference(stream.getvalue())
    calls = []

    def transport(_, tag, endpoint, payload):
        assert endpoint == "/v1/systemone"
        view = json.loads(payload["state"][payload["state"].index("{"):])
        assert view["原行"]["R2"]["C7"] == {"kind": "text", "value": "000014"}
        assert view["原行"]["R2"]["C8"] == {"kind": "number", "value": "14"}
        assert view["原行"]["R3"]["C9"] == {"kind": "text", "value": ""}
        assert view["原行"]["R4"]["C9"] is None
        assert view["candidate_columns"]["C9"]["missing_rows"] == [4]
        calls.append(tag)
        return {"answers": {key: {"choice": value} for key, value in _RIGHT.items()},
                "usage": {"output_tokens": 0}}

    monkeypatch.setattr(model._LoopbackProvider, "_request", transport)
    assert trial(client, route, body).raise_for_status().json()["state"] == "proposal"
    assert calls == list(model._FIRST)


def test_local_reference_checks_host_original_and_revision_before_the_next_request(
        client, monkeypatch, local_reference):
    cid, _, route, preview, body, grant = local_reference(reference_workbook())
    requests = []

    def transport(_, tag, endpoint, payload):
        requests.append((tag, endpoint))
        if endpoint == "/v1/systemone":
            answer = _RIGHT if tag == model._FIRST[0] else _DEFER
            return {"answers": {key: {"choice": value} for key, value in answer.items()},
                    "usage": {"output_tokens": 0}}
        assert endpoint == "/apply-template"
        body = {"mode": preview["mode"], "mapping": preview["mapping"]}
        client.post(route + "/preview", json=body).raise_for_status()
        return {"prompt": "<|im_start|>user\n" + payload["messages"][0]["content"] + "<|im_end|>\n" + model._NO_THINK}

    monkeypatch.setattr(model._LoopbackProvider, "_request", transport)
    monkeypatch.setattr(import_verbatim_balance.sys, "frozen", True, raising=False)
    denied = trial(client, route, body)
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "original_reference_host_unavailable"
    monkeypatch.setattr(import_verbatim_balance.sys, "frozen", False)
    client.app.state.desktop_verbatim_reference_review = replace(grant, source_sha256="0" * 64)
    mismatch = trial(client, route, body)
    assert mismatch.status_code == 409 and mismatch.json()["error"]["code"] == "original_reference_scope_changed"
    client.app.state.desktop_verbatim_reference_review = replace(grant, rows=(195,))
    incomplete = trial(client, route, body)
    assert incomplete.status_code == 422 and incomplete.json()["error"]["code"] == "original_reference_rows_unavailable"
    endpoints = client.app.state.desktop_model_trial
    client.app.state.desktop_model_trial = (None,)
    assert trial(client, route, body).raise_for_status().json()["reason"] == "not_configured"
    assert requests == []
    client.app.state.desktop_model_trial = endpoints
    client.app.state.desktop_verbatim_reference_review = grant
    stale = trial(client, route, body)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "ready_evidence_stale"
    assert requests == [(tag, "/v1/systemone") for tag in model._FIRST] + [(model._RECHECK, "/apply-template")]
    assert client.get(f"/api/v1/cases/{cid}/flows").raise_for_status().json()["total"] == 0


def test_desktop_scope_binds_only_approved_case_bytes_and_first_upload_revision(
        client, make_case, monkeypatch, local_reference):
    raw = reference_workbook()
    cid, other_case = make_case("Approved synthetic case"), make_case("Outside approved scope")
    scope = import_reference_trial.DesktopReferenceScope({
        "case_id": int(cid), "source_sha256": sha256(raw).hexdigest(),
        "member_path": "worksheets/0001", "rows": [2, 3, 4],
    })
    monkeypatch.setattr(client.app.state, "desktop_reference_review_scope", scope, raising=False)
    dispatched = []

    def transport(_, tag, route, payload):
        assert route == "/v1/systemone"
        state = payload["state"]
        dispatched.append("原行" in json.loads(state[state.index("{"):]))
        return {"answers": {key: {"choice": value} for key, value in _DEFER.items()},
                "usage": {"output_tokens": 0}}

    def review(job, body, *, verbatim):
        start = len(dispatched)
        result = trial(client, job, body).raise_for_status().json()
        assert result["state"] == "pending" and result["first_calls"] == 2
        assert dispatched[start:] == [verbatim, verbatim]

    monkeypatch.setattr(model._LoopbackProvider, "_request", transport)
    _, outside_case, _, outside_body = original(client, other_case, raw, "reference.xlsx")
    review(outside_case, outside_body, verbatim=False)

    book = load_workbook(io.BytesIO(raw))
    book.active["J2"] = "Another synthetic original"
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    _, other_source, _, other_body = original(client, cid, stream.getvalue(), "reference.xlsx")
    review(other_source, other_body, verbatim=False)

    _, job, preview, body = original(client, cid, raw, "reference.xlsx")
    review(job, body, verbatim=True)
    assert trial(client, job, {**body, "source_sha256": sha256(raw).hexdigest()}).status_code == 422

    refreshed = client.post(job + "/preview", json={
        "mode": preview["mode"], "mapping": preview["mapping"],
    }).raise_for_status().json()
    revision = refreshed["layout_review"]["revision"]
    assert revision != body["revision"]
    assert trial(client, job, body).status_code == 409
    review(job, {**body, "revision": revision}, verbatim=False)

    _, repeat, _, repeat_body = original(client, cid, raw, "reference.xlsx")
    assert repeat != job
    review(repeat, repeat_body, verbatim=False)


def test_workbook_original_number_proof_preserves_missing_optional_tail_cells(
        client, make_case, monkeypatch):
    calls = []

    class Provider:
        def __init__(self, endpoints):
            assert endpoints is not None

        def reference_policy(self, tag, view, questions, prior=None):
            calls.append(tag)
            optional = view["candidate_columns"]["C9"]
            assert optional["kinds"] == {"text": 2, "missing_physical_cell": 1}
            assert optional["missing_rows"] == [4]
            assert optional["nonempty"] == 1 and optional["lengths"] == {1: 1}
            paired = next(item for item in view["comparisons"] if item["columns"] == ["C8", "C9"])
            assert paired["both_nonempty"] == 1
            return _RIGHT

    monkeypatch.setattr(client.app.state, "desktop_model_trial", ({},), raising=False)
    monkeypatch.setattr(model, "_LoopbackProvider", Provider)
    cid = make_case("Short optional workbook suffix")
    base, route, view, body = original(client, cid, reference_workbook(), "references.xlsx")
    assert view["stats"]["unresolved"] == 3 and view["reference_policy"] is None
    result = trial(client, route, body).raise_for_status().json()
    assert (result["state"], result["reason"]) == ("proposal", "first_agreement")
    assert calls == list(model._FIRST)
    assert result["packet"]["selection"]["refs"] == ["C8", "C7"]
    ready = result["prepared_preview"]
    assert ready["stats"]["accepted"] == 3 and ready["automatic"] is False
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0
    submitted = client.post(route + "/commit", json={
        "mode": ready["mode"], "mapping": ready["mapping"], "mark_accounts": [],
        "ready_revision": ready["layout_review"]["revision"],
    })
    assert submitted.status_code == 200
    assert poll(client, cid, ready["import_id"])["result"]["ok"] == 3
    rows = client.get(base + "/flows").raise_for_status().json()["items"]
    assert {(row["trade_no"], row["amount"]) for row in rows} == {
        ("SYN-T1", "25.12"), ("SYN-T2", "18.31"), ("SYN-T3", "10.02"),
    }


@pytest.mark.parametrize(("malformed", "reason"), [
    ("required_missing", "reference_scope_incomplete"),
    ("extends_past_header", "reference_scope_incomplete"),
    ("second_voucher", "reference_columns_unproved"),
    ("repeated_header", "reference_scope_incomplete"),
    ("second_sheet", "reference_scope_incomplete"),
])
def test_incomplete_or_ambiguous_workbook_reference_scope_remains_manual(client, make_case, monkeypatch, malformed, reason):
    calls = engine(monkeypatch, client, [])
    base, route, _, scope = original(client, make_case(), reference_workbook(malformed=malformed), "references.xlsx")
    safe = trial(client, route, scope).raise_for_status().json()
    assert safe["state"] == "pending" and safe["reason"] == reason
    assert safe["prepared_preview"] is None and calls == []
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


def tab_with_optional_reference_tail(variant=None):
    rows = [
        ["交易时间", "本方账号", "对方账号", "交易金额", "收付标志", "交易币种",
         "凭证号", "交易流水号", "日志号", "任务流水号"],
        ["2041-01-14 08:10:00", "00010", "00021", "25.12", "入", "CNY", "000541", "000001", "", ""],
        ["2041-01-14 08:22:00", "00010", "00022", "18.31", "出", "CNY", "000542", "000002", "", ""],
        ["2041-01-14 08:54:00", "00010", "00023", "10.02", "入", "CNY", "000543", ""],
    ]
    if variant == "lost_primary":
        rows[-1].pop()
    elif variant == "unclassified_tail":
        rows[0][-1] = "资金异常说明"
    elif variant == "nonempty_tail":
        rows[1][-1] = "SYN other value"
    elif variant == "missing_middle":
        rows[1].pop()
    elif variant == "invalid_currency":
        rows[-1][5] = "SYN_UNKNOWN"
    buffer = io.StringIO(newline="")
    csv.writer(buffer, delimiter="\t").writerows(rows)
    return buffer.getvalue().encode(), rows


def test_verified_original_tab_keeps_physical_tail_and_identifiers(client, database, make_case, monkeypatch):
    from app import table_import

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Original TAB number roles")
    raw, cells = tab_with_optional_reference_tail()
    # Replay a v103 source-only journal from before the new original-TAB rule.
    with monkeypatch.context() as previous:
        previous.setattr(table_import, "_tail_reference_supported", lambda sheet: False)
        base, old_route, old, _ = original(client, case, raw)
        assert old["table_summary"]["profile"] == table_import.CSV_TAIL_SOURCE_PROFILE
        assert client.post(old_route + "/commit", json={"mode": old["mode"], "mapping": old["mapping"]}).status_code == 200
        assert poll(client, case, old["import_id"])["result"]["ok"] == 0
    base, route, ready, body = original(client, case, raw)
    assert ready["table_summary"]["profile"] == table_import.CSV_TAIL_REFERENCE_PROFILE
    assert ready["stats"]["accepted"] == 3
    assert not ready["automatic"] and ready["reference_policy_basis"] == "rule_verified_source_columns"
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0
    result = trial(client, route, body).raise_for_status().json()
    assert result["reason"] == "reference_policy_already_selected" and result["first_calls"] == result["recheck_calls"] == 0
    revised = client.post(route + "/layout-review", json={"revision": body["revision"], "member_path": "table.csv",
                         "row_start": 1, "column_start": 1}).raise_for_status().json()
    assert [[cell["value"] for cell in row["cells"]] for row in revised["rows"]] == cells
    replay_payload = {"mode": ready["mode"], "mapping": ready["mapping"], "ready_revision": body["revision"]}
    assert client.post(route + "/preview", json={**replay_payload, "reference_policy": None}).status_code == 422
    replay = client.post(route + "/preview", json={
        **replay_payload, "reference_policy": ready["reference_policy"],
    }).raise_for_status().json()
    assert replay["reference_policy_basis"] == ready["reference_policy_basis"]
    payload = {"mode": ready["mode"], "mapping": ready["mapping"], "mark_accounts": []}
    assert client.post(route + "/commit", json=payload).status_code == 409
    assert client.post(route + "/commit", json={
        **payload, "ready_revision": replay["layout_review"]["revision"],
    }).status_code == 200
    job = poll(client, case, ready["import_id"])
    assert job["stage"] == "done", job
    finished = job["result"]
    assert finished["ok"] == 3
    with database._lock:
        database._con.close()
        database._con = None
    records = client.get(route + "/review", params={"member_path": "table.csv", "view": "all"}).raise_for_status().json()["items"]
    assert [[cell["value"] for cell in row["original"]] for row in records] == cells[1:]
    values = client.get(base + "/flows").raise_for_status().json()["items"]
    assert {(row["trade_no"], row["amount"]) for row in values} == {
        ("000001", "25.12"), ("000002", "18.31"), ("", "10.02")}
    for record, originals in zip(client.get(route + "/records").raise_for_status().json()["items"], cells[1:], strict=True):
        seen = {item["role"]: item["raw"] for item in json.loads(record["details"]["source_field_evidence"])}
        assert seen["source_transaction_id"] == originals[7] and seen["independent_voucher_reference"] == originals[6]
    package = client.get(f"/api/cases/{case}/export").raise_for_status().content
    restored = client.post("/api/cases/import", files={"file": ("tab.fundcase", package)}).raise_for_status().json()
    content = client.get(f"/api/v1/cases/{restored['case_id']}/flows").raise_for_status().json()["items"]
    assert {row["trade_no"] for row in content} == {"000001", "000002", ""}
    retained = base + f"/sources/{ready['import_id']}"
    assert client.get(retained + "/file").content == raw
    assert client.delete(retained + "/operation", params={"expected_batch_id": finished["batch_id"]}).status_code == 200
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0
    assert client.get(retained + "/file").content == raw


@pytest.mark.parametrize(("variant", "expected_status"), [
    ("lost_primary", 400),
    ("nonempty_tail", 400),
    ("missing_middle", 400),
    ("unclassified_tail", 200),
])
def test_reference_tail_does_not_promote_unsafe_shapes(client, make_case, monkeypatch, variant, expected_status):
    from app import table_import

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base = f"/api/v1/cases/{case}"
    raw, _ = tab_with_optional_reference_tail(variant)
    response = client.post(base + "/imports/preview", files={"file": ("negative.csv", raw)})
    assert response.status_code == expected_status
    if expected_status == 200:
        assert response.json()["table_summary"]["profile"] == table_import.CSV_TAIL_SOURCE_PROFILE
        assert response.json()["stats"]["accepted"] == 0
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


def test_reference_tail_still_rejects_a_row_with_unrecognized_currency(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case()
    _, route, ready, body = original(client, cid, tab_with_optional_reference_tail("invalid_currency")[0])
    assert ready["reference_policy_basis"] == "rule_verified_source_columns"
    assert ready["stats"]["accepted"] == 2 and ready["table_summary"]["reason_counts"]["invalid_currency"] == 1
    response = client.post(route + "/commit", json={"mode": ready["mode"], "mapping": ready["mapping"],
                                                  "ready_revision": body["revision"], "mark_accounts": []})
    assert response.status_code == 200
    job = poll(client, cid, ready["import_id"])
    assert job["stage"] == "done", job
    assert job["result"]["ok"] == 2
