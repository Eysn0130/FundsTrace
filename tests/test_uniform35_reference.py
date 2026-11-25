"""Exact business-only form of uniform-37, including stale XLSX dimensions."""
import io
import json
import re
import zipfile

import pytest

from app import import_preview_cache, table_import
from test_automatic_table_import import preview
from test_feedback31_reference import payload
from test_feedback_import_api import complete
from test_folder_evidence import accepted
from test_uniform37_reference import COLUMNS as PADDED_COLUMNS, row


COLUMNS = PADDED_COLUMNS[:-2]


def workbook(records, headers=COLUMNS, stale=False, padding=None):
    raw = payload(records, headers, suffix="xlsx")
    if padding is not None:
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(raw))
        for index, value in enumerate(padding, len(headers) + 1):
            book.active.cell(row=2, column=index, value=value)
        stream = io.BytesIO()
        book.save(stream)
        book.close()
        raw = stream.getvalue()
    if not stale:
        return raw
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for member in source.infolist():
            content = source.read(member)
            if member.filename.startswith("xl/worksheets/") and member.filename.endswith(".xml"):
                content = re.sub(rb'<dimension ref="[^"]+"', b'<dimension ref="A1:A1"', content)
            target.writestr(member, content)
    return output.getvalue()


@pytest.mark.parametrize("stale", [False, True])
@pytest.mark.parametrize("padding", [None, ("", "")])
def test_whole35_workbook_preserves_references_and_reads_physical_rows(client, make_case, stale, padding):
    cid = make_case("Synthetic business-only workbook")
    raw = workbook([row(), row(**{"交易金额": "0", "交易流水号": "SYN-ZERO"})], stale=stale, padding=padding)
    view, receipt = accepted(client, cid, [("rows.xlsx", raw)], scope_bank=None)
    assert view["stats"]["ok"] == receipt["ok"] == 1
    assert receipt["table_summary"]["non_monetary"] == 1
    page = client.get(f"/api/v1/cases/{cid}/flows").json()
    assert page["total"] == 2 and page["funds"]["observation_count"] == 1
    flow = next(item for item in page["items"] if item["trade_no"] == "SYN-PRIMARY")
    zero = next(item for item in page["items"] if item["trade_no"] == "SYN-ZERO")
    assert zero["amount"] == "0.00" and zero["source_ledger"]["economic_effect"] == "no_movement"
    assert flow["trade_no"] == "SYN-PRIMARY" and flow["amount"] == "12.34" and flow["raw_self_account"] == "SYN-A"
    original = client.get(f"/api/v1/cases/{cid}/sources/{receipt['source_id']}/file")
    with zipfile.ZipFile(io.BytesIO(original.content)) as source:
        assert source.read("rows.xlsx") == raw
    package = client.get(f"/api/cases/{cid}/export").content
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package)})
    assert restored.status_code == 200, restored.text
    assert {item["trade_no"] for item in client.get(f"/api/v1/cases/{restored.json()['case_id']}/flows").json()["items"]} == {"SYN-PRIMARY", "SYN-ZERO"}


@pytest.mark.parametrize("headers", [COLUMNS[:-1], COLUMNS + ("extra",),
    tuple("交易流水号" if name == "备注" else name for name in COLUMNS), COLUMNS + ("Unnamed: 35",)])
def test_only_the_exact35_signature_inherits_reference_roles(client, make_case, headers):
    _, result = accepted(client, make_case("Synthetic non-contract workbook"), [("rows.xlsx", workbook([row()], headers, True))], scope_bank=None)
    assert result["ok"] == 0 and result["table_summary"]["unresolved"] == 1


@pytest.mark.parametrize("changes", [{"交易金额": "-12.34"}, {"交易金额": "12.345"}, {"币种": "unknown"},
    {"借贷标志": "unknown"}, {"交易时间": "unknown"}, {"交易是否成功": "unknown"},
    {"本方账号": "", "本方卡号": "", "查询账号": "SYN-QUERY-ONLY"}])
def test_35_reference_roles_do_not_relax_core_values_or_promote_queries(client, make_case, changes):
    _, result = accepted(client, make_case("Synthetic workbook admission controls"), [("rows.xlsx", workbook([row(**changes)], stale=True))], scope_bank=None)
    assert result["ok"] == 0 and result["table_summary"]["unresolved"] == 1


def test_legacy_profile_does_not_inherit35_reference_roles():
    plan = table_import.parse(workbook([row()], stale=True), suffix=".xlsx", profile=table_import.CURRENCY_REGION_PROFILE,
                              reference_policy=table_import.TRANSACTION_REFERENCE_POLICY)
    assert not plan["rows"] and plan["records"][0]["reason_code"] == "conflicting_field"


@pytest.mark.parametrize("padding", [("SYN-NONEMPTY", ""), ("", "SYN-NONEMPTY"), (0, ""), ("", "=1+1"), ("",), ("", "", "")])
def test_only_two_empty_trailing_workbook_coordinates_inherit35_roles(client, make_case, padding):
    _, result = accepted(client, make_case("Synthetic worksheet padding controls"),
                         [("rows.xlsx", workbook([row()], stale=True, padding=padding))], scope_bank=None)
    assert result["ok"] == 0 and result["table_summary"]["unresolved"] == 1


@pytest.mark.parametrize(("headers", "padding"), [(COLUMNS, None), (COLUMNS[::-1], ("", ""))])
def test_new_trial_upload_persists_source_reference_choice_before_confirming(
        client, database, make_case, monkeypatch, headers, padding):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Synthetic trial reference roles")
    raw = workbook([row(), row(**{"交易流水号": "SYN-SECOND"})], headers, stale=True, padding=padding)
    view = preview(client, cid, raw, "synthetic.xlsx")
    base = f"/api/v1/cases/{cid}"
    session = base + f"/imports/{view['import_id']}"
    policy = "transaction-reference-v1"
    basis = "uniform35_source_header"
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"
    assert view["stats"]["accepted"] == 2 and view["reference_policy_available"] is True
    assert (view["reference_policy"], view["reference_policy_basis"], view["automatic"]) == (policy, basis, False)
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0

    if padding:
        import_preview_cache.discard()
        with database._lock:
            database._con.close()
            database._con = None
            database.con()
    [stored] = client.get(base + "/imports", params={"import_id": view["import_id"]}).raise_for_status().json()["items"]
    assert stored["stage"] == "ready"
    assert (stored["request"]["reference_policy"], stored["request"]["reference_policy_basis"]) == (policy, basis)
    body = {"mode": stored["mode"], "mapping": stored["mapping"]}
    if padding:
        view = client.post(session + "/preview", json=body).raise_for_status().json()
        assert (view["reference_policy"], view["reference_policy_basis"]) == (policy, basis)
        assert view["stats"]["accepted"] == 2
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 2
    journal = client.get(session + "/records").raise_for_status().json()["items"]
    for record, ref in zip(journal, ("SYN-PRIMARY", "SYN-SECOND"), strict=True):
        evidence = {entry["role"]: entry for entry in json.loads(record["details"]["source_field_evidence"])}
        assert evidence["source_transaction_id"]["raw"] == ref
        assert evidence["source_transaction_id"]["column_index"] == headers.index("交易流水号") + 1
        assert evidence["independent_voucher_reference"]["raw"] == "SYN-VOUCHER"
        assert evidence["independent_voucher_reference"]["column_index"] == headers.index("凭证号") + 1
    flows = client.get(base + "/flows").raise_for_status().json()
    assert {flow["trade_no"] for flow in flows["items"]} == {"SYN-PRIMARY", "SYN-SECOND"}
    assert flows["funds"]["confirmed_event_count"] == 0
    assert client.get(base + f"/sources/{view['import_id']}/file").content == raw
    [finished] = client.get(base + "/imports", params={"import_id": view["import_id"]}).raise_for_status().json()["items"]
    assert finished["request"]["reference_policy_basis"] == basis
    if padding:
        package = client.get(f"/api/cases/{cid}/export").raise_for_status().content
        reopened = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package)}).raise_for_status().json()
        readback = client.get(f"/api/v1/cases/{reopened['case_id']}/flows").raise_for_status().json()
        assert {flow["trade_no"] for flow in readback["items"]} == {"SYN-PRIMARY", "SYN-SECOND"}
        assert readback["funds"]["confirmed_event_count"] == 0


def test_reference_choice_respects_old_ready_explicit_disable_and_manual_enable(client, make_case, monkeypatch):
    raw = workbook([row()])
    old = preview(client, make_case("Old reference session"), raw, "synthetic.xlsx")
    assert old["reference_policy"] is old["reference_policy_basis"] is None
    assert old["table_summary"]["unresolved"] == 1

    def repreview(view, **overrides):
        base = f"/api/v1/cases/{view['case_id']}/imports/{view['import_id']}"
        return client.post(base + "/preview", json={"mode": view["mode"], "mapping": view["mapping"],
                                                   **overrides}).raise_for_status().json()

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    old_again = repreview(old)
    assert old_again["reference_policy"] is old_again["reference_policy_basis"] is None
    assert old_again["table_summary"]["unresolved"] == 1
    view = preview(client, make_case("New reference session"), raw, "synthetic.xlsx")
    assert view["reference_policy_basis"] == "uniform35_source_header"
    base = f"/api/v1/cases/{view['case_id']}/imports/{view['import_id']}"
    assert client.post(base + "/preview", json={
        "mode": view["mode"], "mapping": view["mapping"], "reference_policy": "transaction-reference-v1",
        "reference_policy_basis": "uniform35_source_header",
    }).status_code == 422
    user_confirmed = repreview(view, reference_policy="transaction-reference-v1")
    assert user_confirmed["reference_policy"] == "transaction-reference-v1"
    assert user_confirmed["reference_policy_basis"] is None
    cleared = repreview(user_confirmed, reference_policy=None)
    assert cleared["reference_policy"] is cleared["reference_policy_basis"] is None
    assert cleared["table_summary"]["unresolved"] == 1
    still_cleared = repreview(cleared)
    assert still_cleared["reference_policy"] is still_cleared["reference_policy_basis"] is None
    manual = repreview(still_cleared, reference_policy="transaction-reference-v1")
    assert manual["reference_policy"] == "transaction-reference-v1"
    assert manual["reference_policy_basis"] is None and manual["stats"]["accepted"] == 1


@pytest.mark.parametrize("change", ["repeated_region", "second_sheet", "similar_header", "duplicate_role", "filled_tail"])
def test_other_workbooks_do_not_select_reference_policy(client, make_case, monkeypatch, change):
    from openpyxl import load_workbook

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    book = load_workbook(io.BytesIO(workbook([row()])))
    sheet = book.active
    if change == "repeated_region":
        sheet.append(COLUMNS)
        sheet.append([row().get(name) for name in COLUMNS])
    elif change == "second_sheet":
        book.copy_worksheet(sheet)
    elif change == "similar_header":
        sheet.cell(1, COLUMNS.index("交易类型") + 1).value = "交易类型说明"
    elif change == "duplicate_role":
        column = COLUMNS.index("交易类型") + 1
        sheet.cell(1, column).value = "交易流水号"
        sheet.cell(2, column).value = "SYN-CONFLICTING-REFERENCE"
    else:
        sheet.cell(2, 37).value = "=1+1"
    output = io.BytesIO()
    book.save(output)
    book.close()
    view = preview(client, make_case("Synthetic unsupported reference scope"), output.getvalue(), "synthetic.xlsx")
    assert view["reference_policy"] is view["reference_policy_basis"] is None
    assert view["stats"]["accepted"] == 0 and view["table_summary"]["unresolved"] > 0
    if change == "duplicate_role":
        session = f"/api/v1/cases/{view['case_id']}/imports/{view['import_id']}"
        manual = client.post(session + "/preview", json={
            "mode": view["mode"], "mapping": view["mapping"], "reference_policy": "transaction-reference-v1",
        }).raise_for_status().json()
        assert manual["reference_policy_basis"] is None
        assert manual["table_summary"]["reason_counts"] == {"conflicting_field": 1}
