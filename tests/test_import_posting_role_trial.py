"""Ordinary source-only role consideration, verification, persistence and isolation."""
import csv
import io

import pytest
from openpyxl import Workbook

from app.services import desktop_model_trial, imports
from test_automatic_table_import import preview
from test_feedback_import_api import complete


def source(*, change=None):
    columns = ["本人账号", "存取", "发生额", "本次余额", "对方账号", "摘要", "币种", "备注", "对方户名"]
    rows = [
        ["00000007123", "存", "17.25", "43.10", "", "Synthetic private narrative", "", "", ""],
        ["00000007123", "取", "23.75", "92.10", "", "Other private narrative", "", "", ""],
        ["00000007123", "取", "4.58", "91.10", "", "Third private narrative"],
    ]
    if change == "negative":
        rows[1][2] = "-23.75"
    elif change == "missing_role_cell":
        rows[2] = rows[2][:2]
    elif change == "unknown_marker":
        rows[1][1] = "另"
    elif change == "alternate_account":
        rows[1][0] = "00000007456"
    elif change == "competing_owner_header":
        columns.append("本方账号")
        for row in rows:
            row.extend([""] * (len(columns) - len(row) - 1))
            row.append("00000007456")
    elif change == "competing_header":
        columns[-1] = "发生额"
    elif change == "amount_in_other_roles":
        columns[2] = "另记余额"
    elif change == "direction_in_summary":
        columns[1] = "备注"
    elif change == "competing_declared_direction":
        columns.append("收付标志")
        for row in rows:
            row.extend([""] * (len(columns) - len(row) - 1))
            row.append("入")
    elif change == "continued_header":
        rows.insert(1, columns.copy())
    elif change == "boundary_alternatives":
        columns.extend(("对手帐号", "币别", "现金标志", "交易状态"))
        for row in rows:
            row.extend([""] * (len(columns) - 4 - len(row)))
            row.extend(("00000008991", "CNY", "1", "成功"))
    data = io.StringIO(newline="")
    csv.writer(data, delimiter="\t").writerows([columns, *rows])
    return data.getvalue().encode()


def ready(client, case, content=None):
    view = preview(client, case, content if content is not None else source())
    assert view["table_summary"]["accepted"] == 0
    assert view["layout_review"]["members"][0]["header_rows"] == []
    address = f"/api/v1/cases/{case}/imports/{view['import_id']}"
    body = {"revision": view["layout_review"]["revision"], "member_path": "table.csv", "header_row": 1,
            "field": "own_direction_amount"}
    return view, address, body


def no_models(client, monkeypatch, *, configured=False):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (object() if configured else None,), raising=False)
    monkeypatch.setattr(desktop_model_trial, "_LoopbackProvider", lambda *_: pytest.fail("closed source rules have no dispatch"))


@pytest.mark.parametrize("mode", ["rules", "single_qwen", "two_plus_one"])
def test_source_posting_rules_answer_on_intake_and_survive_original_custody(
        client, database, make_case, monkeypatch, mode):
    no_models(client, monkeypatch, configured=mode != "rules")
    case = make_case()
    raw = source()
    view, address, body = ready(client, case, raw)
    shown = client.get(address + "/intake").raise_for_status().json()["source_field_notes"]
    assert shown[0]["rows"] == [2] and shown[1]["rows"] == [3, 4]
    assert ("入账" in shown[0]["text"] and "出账" in shown[1]["text"] and "发生额" in shown[1]["text"]
            and "现金属性" in shown[-1]["text"] and "交易效力" in shown[-1]["text"])
    assert any(note["columns"] == [7] and note["rows"] == [4] and "原格缺失" in note["text"] for note in shown)
    assert all("00000007123" not in note["text"] and "17.25" not in note["text"] for note in shown)
    if mode == "single_qwen":
        body["decision_mode"] = mode
    result = client.post(address + "/layout-review/model-trial", json=body).raise_for_status().json()
    assert (result["state"], result["reason"], result["first_calls"], result["recheck_calls"]) == (
        "rule", "source_posting_roles_rule", 0, 0)
    assert result["prepared_preview"] is None and result["receipt"] is None
    facts = result["packet"]["facts"]
    assert facts["case_id"] == str(case) and facts["import_id"] == view["import_id"] and facts["revision"] == body["revision"]
    assert facts["field"] == body["field"] and facts["member_path"] == "table.csv" and facts["header_row"] == 1
    expected = {"scope": "source_only", "direction_column": 2, "amount_column": 3, "financial_admission": False,
                "rows": [{"source_row": 2, "direction": "in", "amount_column": 3},
                         {"source_row": 3, "direction": "out", "amount_column": 3},
                         {"source_row": 4, "direction": "out", "amount_column": 3}]}
    assert facts["source_posting_roles"] == expected
    assert {record["id"] for record in result["packet"]["records"]} == {"C1", "C2", "C3", "R2", "R3", "R4"}
    assert client.get(address + "/intake").raise_for_status().json()["source_field_notes"] == shown
    finished = complete(client, case, view)
    assert finished["stage"] == "done" and finished["result"]["ok"] == 0
    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    assert client.get(address + "/intake").raise_for_status().json()["source_field_notes"] == shown
    base = f"/api/v1/cases/{case}"
    original = f"{base}/sources/{view['import_id']}"
    assert client.get(original + "/file").content == raw
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0
    assert client.delete(original + "/operation", params={"expected_batch_id": finished["result"]["batch_id"]}).status_code == 200
    assert "source_field_notes" not in client.get(address + "/intake").raise_for_status().json()


@pytest.mark.parametrize("variant", ["negative", "missing_role_cell", "unknown_marker", "alternate_account",
    "competing_owner_header", "competing_header", "continued_header", "amount_in_other_roles", "direction_in_summary",
    "competing_declared_direction"])
def test_source_posting_rule_withholds_unproved_scope_without_dispatch(client, make_case, monkeypatch, variant):
    no_models(client, monkeypatch, configured=True)
    case = make_case()
    _, address, body = ready(client, case, source(change=variant))
    value = client.post(address + "/layout-review/model-trial", json=body).raise_for_status().json()
    assert (value["state"], value["reason"], value["first_calls"], value["recheck_calls"]) == (
        "pending", "source_posting_roles_unproved", 0, 0)
    assert "source_posting_roles" not in value["packet"]["facts"]
    notes = client.get(address + "/intake").raise_for_status().json().get("source_field_notes", [])
    assert all("入账" not in note["text"] and "出账" not in note["text"] for note in notes)


def test_posting_roles_preserve_old_entry_and_require_current_local_source(client, make_case, monkeypatch):
    no_models(client, monkeypatch)
    case, other = make_case("Synthetic posting-role source"), make_case("Synthetic other case")
    view, address, body = ready(client, case)
    result = client.post(address + "/layout-review/model-trial", json=body).raise_for_status().json()
    assert (result["state"], result["reason"], result["first_calls"]) == ("rule", "source_posting_roles_rule", 0)
    assert client.post(address + "/layout-review/assistance", json=body).status_code == 422
    assert client.post(address + "/layout-review/model-trial", json={**body, "prepare_layout": True}).status_code == 422
    assert client.post(address + "/layout-review/model-trial", json={**body, "revision": "f" * 32}).status_code == 409
    assert client.post(address + "/layout-review/model-trial", json={**body, "header_row": 2}).status_code == 422
    across = f"/api/v1/cases/{other}/imports/{view['import_id']}/layout-review/model-trial"
    assert client.post(across, json=body).status_code == 404
    path = imports._path(view["import_id"], ".csv")
    raw = path.read_bytes()
    changed = raw.replace(b"17.25", b"18.25", 1)
    assert changed != raw and len(changed) == len(raw)
    path.write_bytes(changed)
    altered = client.post(address + "/layout-review/model-trial", json=body)
    assert altered.status_code == 409 and altered.json()["error"]["code"] == "source_changed"


def test_posting_rule_rejects_stale_revision_and_rereads_new_original_scope(client, make_case, monkeypatch):
    no_models(client, monkeypatch)
    case = make_case()
    view, address, body = ready(client, case)
    accepted = client.post(address + "/layout-review/model-trial", json=body).raise_for_status().json()
    assert accepted["state"] == "rule"
    before = client.get(address + "/intake").raise_for_status().json()["source_field_notes"]
    prepared = client.post(address + "/preview", json={"mode": view["mode"], "mapping": view["mapping"]}).raise_for_status().json()
    revision = prepared["layout_review"]["revision"]
    assert revision != body["revision"]
    assert client.post(address + "/layout-review/model-trial", json=body).status_code == 409
    current = client.post(address + "/layout-review/model-trial", json={**body, "revision": revision}).raise_for_status().json()
    assert (current["state"], current["reason"]) == ("rule", "source_posting_roles_rule")
    assert current["packet"]["facts"]["revision"] == revision
    assert client.get(address + "/intake").raise_for_status().json()["source_field_notes"] == before
    assert complete(client, case, prepared)["result"]["ok"] == 0


def test_source_only_action_does_not_relabel_a_registered_financial_layout(client, make_case, monkeypatch):
    no_models(client, monkeypatch, configured=True)
    case = make_case()
    raw = ("交易时间,本方账号,对方账号,交易金额,收付标志,交易币种\n"
           "2037-12-01 11:00:00,SYN-A,SYN-B,13.50,出,CNY\n").encode()
    view = preview(client, case, raw)
    assert view["table_summary"]["accepted"] == 1
    scope = view["layout_review"]
    assert scope["members"][0]["header_rows"] == [1]
    target = f"/api/v1/cases/{case}/imports/{view['import_id']}/layout-review/model-trial"
    finished = client.post(target, json={"revision": scope["revision"], "member_path": "table.csv", "header_row": 1,
                                         "field": "own_direction_amount"}).raise_for_status().json()
    assert (finished["state"], finished["reason"], finished["first_calls"]) == ("pending", "source_posting_already_registered", 0)
    assert "source_posting_roles" not in finished["packet"]["facts"]


def test_source_boundaries_preserve_other_supported_columns_when_original_fields_are_blank(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    view, address, _ = ready(client, case, source(change="boundary_alternatives"))
    result = client.get(address + "/intake").raise_for_status().json()
    notes = result["source_field_notes"]
    for physical in (5, 7, 9):
        assert any(note["columns"] == [physical] and "原格为空" in note["text"] for note in notes)
    for physical in (10, 11, 12, 13):
        assert any(note["columns"] == [physical] and note["rows"] == [2, 3, 4] and "有原值" in note["text"] for note in notes)
    assert any("整区尚无正式财务准入" in note["text"] for note in notes)
    assert all("00000008991" not in note["text"] and "成功" not in note["text"] for note in notes)
    assert complete(client, case, view)["result"]["ok"] == 0
    assert client.get(address + "/intake").raise_for_status().json()["source_field_notes"] == notes


def registered_direction(*, marker="借/付", other_owner=False, sign_note=False, currency="CNY"):
    header = ["交易账号", "交易时间", "交易币种", "交易金额", "借贷标志", "余额", "现转标志", "摘要", "对方户名"]
    rows = [
        ["00000001234", "2037-03-02 09:00:01", currency, "15.27", "贷/收", "100.71", "转账", "private memo", "收"],
        ["00000001234", "2037-03-02 09:01:02", currency, "10.11", marker, "90.60", "转账", "private name", "借"],
    ]
    if other_owner:
        header.append("本方账号")
        for row in rows:
            row.append("00000009999")
    if sign_note:
        header.append("金额符号约定")
        rows[0].append("本表交易金额正数表示本方收入，绝对值为元；负数表示本方支出。")
        rows[1].append("")
    buffer = io.StringIO(newline="")
    csv.writer(buffer).writerows([header, *rows])
    return buffer.getvalue().encode()


def registered_ready(client, case, raw=None):
    view = preview(client, case, registered_direction() if raw is None else raw)
    address = f"/api/v1/cases/{case}/imports/{view['import_id']}"
    scope = view["layout_review"]
    body = {"revision": scope["revision"], "member_path": "table.csv", "header_row": 1,
            "field": "own_direction_amount"}
    return view, address, body


@pytest.mark.parametrize("mode", ["rules", "single_qwen", "two_plus_one"])
def test_registered_direction_rule_appears_on_original_before_action_and_survives_cold_read(
        client, database, make_case, monkeypatch, mode):
    no_models(client, monkeypatch, configured=mode != "rules")
    case = make_case()
    view, address, body = registered_ready(client, case)
    assert view["layout_review"]["members"][0]["header_rows"] == [1]
    assert view["table_summary"]["reason_counts"] == {"invalid_direction": 2}
    notes = client.get(address + "/intake").raise_for_status().json()["source_field_notes"]
    assert any(note["columns"] == [1, 4, 5] and note["rows"] == [2] and "本方入账" in note["text"] for note in notes)
    assert any(note["columns"] == [1, 4, 5] and note["rows"] == [3] and "本方出账" in note["text"] for note in notes)
    assert any("整区尚无正式财务准入" in note["text"] for note in notes)
    assert all("收付仍待核" not in note["text"] and "适用于本表区的币种码表" not in note["text"] for note in notes)
    if mode == "single_qwen":
        body["decision_mode"] = mode
    result = client.post(address + "/layout-review/model-trial", json=body).raise_for_status().json()
    assert result["prepared_preview"] is None and result["receipt"] is None
    assert (result["state"], result["reason"], result["first_calls"], result["recheck_calls"]) == (
        "rule", "source_posting_roles_rule", 0, 0)
    assert "model_citations" not in result["packet"]["facts"]
    roles = result["packet"]["facts"]["source_posting_roles"]
    assert (roles["direction_column"], roles["amount_column"], roles["financial_admission"]) == (5, 4, False)
    assert roles["rows"] == [{"source_row": 2, "direction": "in", "amount_column": 4},
                             {"source_row": 3, "direction": "out", "amount_column": 4}]
    finished = complete(client, case, view)
    assert finished["result"]["ok"] == 0
    with database._lock:
        database._con.close()
        database._con = None
    assert client.get(address + "/intake").raise_for_status().json()["source_field_notes"] == notes
    written = client.get(address + "/records").raise_for_status().json()["items"]
    assert [(row["source_row"], row["reason_code"], row["flow_id"]) for row in written] == [
        (2, "invalid_direction", None), (3, "invalid_direction", None)]
    original = f"/api/v1/cases/{case}/sources/{view['import_id']}"
    assert client.delete(original + "/operation", params={"expected_batch_id": finished["result"]["batch_id"]}).status_code == 200
    assert "source_field_notes" not in client.get(address + "/intake").raise_for_status().json()


def test_source_direction_explanation_preserves_independently_unresolved_currency(client, make_case, monkeypatch):
    no_models(client, monkeypatch, configured=True)
    case = make_case()
    view, address, _ = registered_ready(client, case, registered_direction(currency="999"))
    notes = client.get(address + "/intake").raise_for_status().json()["source_field_notes"]
    assert any(note["columns"] == [5] and note["rows"] == [2, 3] and "收付已按原件核明" in note["text"] for note in notes)
    assert any(note["columns"] == [3] and note["rows"] == [2, 3] and "币种码表" in note["text"] for note in notes)
    assert all("收付仍待核" not in note["text"] and "999" not in note["text"] for note in notes)
    assert complete(client, case, view)["result"]["ok"] == 0
    records = client.get(address + "/review", params={"member_path": "table.csv"}).raise_for_status().json()["items"]
    for record in (entry for entry in records if entry["source_row"] in (2, 3)):
        assert record["reason_code"] == "invalid_direction" and record["flow_id"] is None
        assert "invalid_currency" in record["details"]["validation_issues"].split(",")
        assert record["original"][2]["value"] == "999"


@pytest.mark.parametrize("continued_material", [False, True])
def test_transfer_method_note_on_retained_workbook_does_not_assign_direction(client, make_case, monkeypatch, continued_material):
    no_models(client, monkeypatch, configured=True)
    case = make_case()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["交易时间", "交易金额", "余额", "现转标志", "对手帐号"])
    sheet.append(["2037-03-02 09:00:01", "15.27", "100.71", "转账", ""])
    sheet.append(["2037-03-02 09:01:02", "10.11", "90.60", "转账"])
    if continued_material:
        sheet.append(["材料说明", "参考数值", "参考余额", "摘要", "后续注记"])
        sheet.append(["后附", "29.10", "68.20", "转账", "原表外说明"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    view = preview(client, case, buffer.getvalue(), "synthetic.xlsx")
    assert view["table_summary"]["accepted"] == 0
    address = f"/api/v1/cases/{case}/imports/{view['import_id']}"
    notes = client.get(address + "/intake").raise_for_status().json()["source_field_notes"]
    assert any(note["columns"] == [4] and note["rows"] == [2, 3] and "转账" in note["text"]
               and "方式" in note["text"] for note in notes)
    assert all("本方入账" not in note["text"] and "本方出账" not in note["text"] for note in notes)
    assert complete(client, case, view)["result"]["ok"] == 0
    assert client.get(address + "/intake").raise_for_status().json()["source_field_notes"] == notes


@pytest.mark.parametrize("marker", ["借/收", "贷/付", "未解释", "转账"])
def test_registered_direction_rule_withholds_an_incomplete_or_conflicting_table(client, make_case, monkeypatch, marker):
    no_models(client, monkeypatch, configured=True)
    _, address, body = registered_ready(client, make_case(), registered_direction(marker=marker))
    reply = client.post(address + "/layout-review/model-trial", json=body).raise_for_status().json()
    assert reply["state"] == "pending" and reply["reason"] == "source_posting_roles_unproved"
    assert reply["first_calls"] == reply["recheck_calls"] == 0
    assert reply["prepared_preview"] is None and reply["receipt"] is None
    assert "source_posting_roles" not in reply["packet"]["facts"]
    notes = client.get(address + "/intake").raise_for_status().json().get("source_field_notes", ())
    assert not any("本方入账" in note["text"] or "本方出账" in note["text"] for note in notes)
    assert any("收付仍待核" in note["text"] for note in notes)


def test_registered_direction_review_is_invalidated_by_new_preview(client, make_case, monkeypatch):
    no_models(client, monkeypatch, configured=True)
    case, wrong_case = make_case("Synthetic direction source"), make_case("Synthetic other direction case")
    view, address, body = registered_ready(client, case)
    endpoint = address + "/layout-review/model-trial"
    body["decision_mode"] = "single_qwen"
    wrong = endpoint.replace(f"/cases/{case}/", f"/cases/{wrong_case}/")
    assert client.post(wrong, json=body).status_code == 404
    assert client.post(endpoint, json={**body, "header_row": 2}).status_code == 422
    assert client.post(endpoint, json=body).raise_for_status().json()["state"] == "rule"
    mapping = {**view["mapping"], "summary": "现转标志"}
    response = client.post(address + "/preview", json={"mode": view["mode"], "mapping": mapping})
    repeated = response.raise_for_status().json()
    assert repeated["layout_review"]["revision"] != body["revision"]
    assert client.post(endpoint, json=body).status_code == 409
    assert client.post(endpoint, json={**body, "revision": repeated["layout_review"]["revision"]}).json()["state"] == "pending"
    notes = client.get(address + "/intake").raise_for_status().json().get("source_field_notes", ())
    assert not any("本方入账" in note["text"] or "本方出账" in note["text"] for note in notes)


def test_registered_direction_with_competing_own_account_or_changed_bytes_does_not_dispatch(client, make_case, monkeypatch):
    no_models(client, monkeypatch, configured=True)
    case = make_case()
    _, address, body = registered_ready(client, case, registered_direction(other_owner=True))
    response = client.post(address + "/layout-review/model-trial", json=body).raise_for_status().json()
    assert response["first_calls"] == 0 and "source_posting_roles" not in response["packet"]["facts"]
    view, address, body = registered_ready(client, case)
    original = imports._path(view["import_id"], ".csv")
    raw = original.read_bytes()
    changed = raw.replace(b"15.27", b"15.99", 1)
    assert len(raw) == len(changed) and raw != changed
    original.write_bytes(changed)
    response = client.post(address + "/layout-review/model-trial", json=body)
    assert response.status_code == 409 and response.json()["error"]["code"] == "source_changed"


@pytest.mark.parametrize("sign_basis", ["signed_header", "sign_note"])
def test_signed_amount_with_opposite_original_direction_has_no_source_role_receipt(
        client, database, make_case, monkeypatch, sign_basis):
    no_models(client, monkeypatch, configured=True)
    raw = registered_direction(sign_note=sign_basis == "sign_note")
    if sign_basis == "signed_header":
        raw = raw.replace("交易金额".encode(), "带符号金额".encode(), 1)
    case = make_case()
    view, address, body = registered_ready(client, case, raw)
    assert view["table_summary"]["reason_counts"] == {"invalid_direction": 2}
    reply = client.post(address + "/layout-review/model-trial", json=body).raise_for_status().json()
    assert reply["state"] == "pending" and reply["first_calls"] == reply["recheck_calls"] == 0
    assert "source_posting_roles" not in reply["packet"]["facts"]
    assert complete(client, case, view)["result"]["ok"] == 0
    with database._lock:
        database._con.close()
        database._con = None
    if sign_basis == "sign_note":
        records = client.get(address + "/records").raise_for_status().json()["items"]
        assert any("source_sign_declaration" in record["details"] for record in records)
    notes = client.get(address + "/intake").raise_for_status().json().get("source_field_notes", ())
    assert not any("本方入账" in note["text"] or "本方出账" in note["text"] for note in notes)
