"""Keep ordinary source amounts and revision-bound monetary evidence separate."""

import csv
import io
import json
import zipfile

from openpyxl import Workbook
import pytest

from app import import_preview_cache, table_import
from app.services import imports
from test_api_v1 import poll
from test_folder_evidence import complete


NEW_CONVERTED = ("欧元换算金额", "折合美元金额", "折合美元（元）", "本币等值金额")
DIRECTIONAL_AMOUNTS = ("转入货币金额", "转出相关货币金额")
REFERENCE_AMOUNTS = (*NEW_CONVERTED, *DIRECTIONAL_AMOUNTS)
HEADINGS = ["交易账号", "对方账号", "交易时间", "借贷标志", "币种", "交易金额",
            "交易金额折人民币", "交易金额折美元", "换算金额", "RMB换算金额", "换算RMB金额", *REFERENCE_AMOUNTS]


def _upload(client, make_case, *, first_currency="USD", first_amount="", currency_heading="币种"):
    text = io.StringIO(newline="")
    csv.writer(text, delimiter="\t").writerows([
        [*HEADINGS[:4], currency_heading, *HEADINGS[5:]],
        ["100001", "100002", "2040-09-27 13:55:10", "借", first_currency, first_amount, "90.00", "12.00", "86.00", "91.00", "92.00",
         *(["90.00"] * len(REFERENCE_AMOUNTS))],
        ["100001", "100003", "2040-09-27 13:56:20", "借", "USD", "25.00", "75.00", "25.00", "74.00", "76.00", "77.00",
         *(["75.00"] * len(REFERENCE_AMOUNTS))],
    ])
    case = make_case()
    base = f"/api/v1/cases/{case}"
    ready = client.post(base + "/imports/preview", files={"file": ("conversion.csv", text.getvalue().encode())}).raise_for_status().json()
    return case, base, base + f"/imports/{ready['import_id']}", ready


def _selection(ready):
    return {"mode": ready["mode"], "mapping": ready["mapping"], "ready_revision": ready["layout_review"]["revision"]}


@pytest.mark.parametrize(("field", "converted"), [
    ("amount", "交易金额折人民币"), ("amount", "交易金额折美元"), ("amount", "换算金额"),
    ("amount", "RMB换算金额"), ("amount", "换算RMB金额"),
    ("income_amt", "交易金额折人民币"), ("expense_amt", "交易金额折美元"),
    ("balance", "RMB换算金额"), ("currency", "交易金额折美元"), *[("amount", heading) for heading in NEW_CONVERTED],
    *[(field, heading) for heading in DIRECTIONAL_AMOUNTS for field in ("amount", "income_amt", "expense_amt", "balance", "currency")],
])
def test_named_current_mapping_rejects_explicit_nonposting_money(client, make_case, monkeypatch, field, converted):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    _, _, route, ready = _upload(client, make_case)
    request = _selection(ready)
    request["mapping"] = {**request["mapping"], field: converted}
    rejected = client.post(route + "/preview", json=request)
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "invalid_mapping"


def test_a_previously_ready_named_conversion_is_blocked_before_confirmation(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case, base, route, ready = _upload(client, make_case)
    request = _selection(ready)
    original = request["mapping"]
    request["mapping"] = {**original, "amount": "交易金额折人民币"}
    with monkeypatch.context() as previous:
        previous.setattr(table_import, "explicit_converted_amount_currency", lambda _: None, raising=False)
        old = client.post(route + "/preview", json=request).raise_for_status().json()
    assert any(row["source_row"] == 2 and row["amount"] == "90.00" for row in old["preview"])
    commit = client.post(route + "/commit", json=_selection(old))
    assert commit.status_code == 422 and commit.json()["error"]["code"] == "invalid_mapping"
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0

    restored = client.post(route + "/preview", json={**_selection(old), "mapping": original}).raise_for_status().json()
    assert client.post(route + "/commit", json=_selection(restored)).raise_for_status().json()["started"]
    receipt = poll(client, case, ready["import_id"])["result"]
    assert receipt["ok"] == 1
    [flow] = client.get(base + "/flows").raise_for_status().json()["items"]
    assert flow["amount"] == "25.00" and flow["currency"] == "USD"


@pytest.mark.parametrize("already_ready", [False, True])
def test_converted_raw_cell_cannot_be_a_physical_balance_mapping(client, make_case, monkeypatch, already_ready):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    _, base, route, ready = _upload(client, make_case, first_amount="12.00")
    monkeypatch.setattr(client.app.state, "desktop_model_trial", None)
    region = {"member_path": "table.csv", "header_row": 1, "last_row": 3, "mode": "B",
              "mapping": {"self_account": 1,"counter_account": 2,"trade_time": 3,"direction": 4,
                          "currency": 5,"amount": 6,"balance": 7}}
    action = route + "/layout-review/apply"
    payload = {"revision": ready["layout_review"]["revision"], "regions": [region], "confirmed": True}
    if not already_ready:
        response = client.post(action, json=payload)
        assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_table_file"
    else:
        with monkeypatch.context() as previous:
            previous.setattr(table_import, "explicit_converted_amount_currency", lambda _: None, raising=False)
            old = client.post(action, json=payload).raise_for_status().json()
        response = client.post(route + "/commit", json=_selection(old))
        assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_mapping"
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


def test_saved_secondary_region_cannot_write_a_converted_balance(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    workbook = Workbook()
    first, second = workbook.active, workbook.create_sheet("Second")
    first.title = "First"
    for day, sheet in enumerate((first, second), 11):
        sheet.append(HEADINGS)
        sheet.append(["100001", "100002", f"2040-09-{day} 13:55:10", "借", "USD", "12.00", "90.00",
                      "12.00", "86.00", "91.00", "92.00", *(["90.00"] * len(REFERENCE_AMOUNTS))])
    buffer = io.BytesIO()
    workbook.save(buffer)
    base = f"/api/v1/cases/{make_case()}"
    ready = client.post(base + "/imports/preview", files={"file": ("regions.xlsx", buffer.getvalue())}).raise_for_status().json()
    route = base + f"/imports/{ready['import_id']}"
    layout = ready["layout_review"]
    mapping = {"self_account": 1, "counter_account": 2, "trade_time": 3, "direction": 4, "currency": 5, "amount": 6}
    regions = [{"member_path": member["member_path"], "header_row": 1, "last_row": 2, "mode": "B",
                "mapping": {**mapping, **({"balance": 7} if member["sheet"] == "Second" else {})}}
               for member in layout["members"]]
    assert len(regions) == 2
    with monkeypatch.context() as previous:
        previous.setattr(table_import, "explicit_converted_amount_currency", lambda _: None, raising=False)
        old = client.post(route + "/layout-review/apply", json={"revision": layout["revision"], "regions": regions,
                                                                 "confirmed": True}).raise_for_status().json()
    assert "balance" not in old["mapping"]
    rejected = client.post(route + "/commit", json=_selection(old))
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "invalid_mapping"
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


def test_saved_monetary_evidence_rejects_missing_and_stale_revision(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case, base, route, ready = _upload(client, make_case, first_amount="12.00")
    assert any(note.get("money_role") for note in client.get(route + "/intake").raise_for_status().json()["source_field_notes"])
    current = client.post(route + "/preview", json=_selection(ready)).raise_for_status().json()
    request = _selection(current)
    assert request["ready_revision"] != ready["layout_review"]["revision"]
    missing = client.post(route + "/commit", json={k: v for k, v in request.items() if k != "ready_revision"})
    assert missing.status_code == 409 and missing.json()["error"]["code"] == "preview_changed"
    stale = client.post(route + "/commit", json=_selection(ready))
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "ready_evidence_stale"
    assert client.post(route + "/commit", json=request).raise_for_status().json()["started"]
    assert poll(client, case, ready["import_id"])["result"]["ok"] == 2
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 2
    assert not client.post(route + "/commit", json={k: v for k, v in request.items() if k != "ready_revision"}).raise_for_status().json()["started"]


@pytest.mark.parametrize("currency_heading", ["币种", "交易货币", "货币码", "币种码", "币别码", "币种代码"])
@pytest.mark.parametrize(("raw_currency", "expected_currency"), [("", "CNY"), ("XX7", None), ("USD", "USD")])
def test_source_currency_heading_distinguishes_default_invalid_and_explicit(
        client, make_case, monkeypatch, currency_heading, raw_currency, expected_currency):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case, _, route, ready = _upload(client, make_case, first_currency=raw_currency, first_amount="12.00",
                                  currency_heading=currency_heading)
    client.post(route + "/commit", json=_selection(ready)).raise_for_status()
    assert poll(client, case, ready["import_id"])["result"]["ok"] == (2 if expected_currency else 1)
    rows = client.get(route + "/review", params={"member_path": "table.csv"}).raise_for_status().json()["items"]
    row = next(item for item in rows if item["source_row"] == 2)
    assert next(item for item in row["original"] if item["column"] == 5)["value"] == raw_currency
    basis = row["details"].get("currency_basis", "")
    if expected_currency:
        assert row["flow_id"] and row["preview"]["currency"] == expected_currency
        assert ("来源未提供币种" in basis) == (not raw_currency)
    else:
        assert row["flow_id"] is None and row["reason_code"] == "invalid_currency"
        assert "来源已报告币种" in basis and "默认" not in basis and "未提供" not in basis


def _remove_preview_currency_version(database, import_id):
    with database.transaction():
        [saved] = database.q_raw("select request_json from import_sessions where import_id=?", [import_id])
        persisted = json.loads(saved["request_json"])
        assert persisted.pop(imports.SOURCE_CURRENCY_INTERPRETATION_KEY) == imports.SOURCE_CURRENCY_INTERPRETATION_VERSION
        database.x("update import_sessions set request_json=? where import_id=?", [json.dumps(persisted), import_id])


@pytest.mark.parametrize(("raw_currency", "new_count"), [("USD", 2), ("XX7", 1)])
def test_old_ready_currency_interpretation_requires_a_new_preview(client, database, make_case, monkeypatch, raw_currency, new_count):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    with monkeypatch.context() as previous:
        previous.setattr(table_import, "SOURCE_CURRENCY_HEADINGS", frozenset({"币种代码"}))
        case, base, route, old = _upload(client, make_case, currency_heading="交易货币", first_currency=raw_currency, first_amount="12.00")
    assert old["stats"]["ok"] == 2 and {row["currency"] for row in old["preview"]} == {"CNY"}
    assert "currency" not in old["mapping"]
    _remove_preview_currency_version(database, old["import_id"])
    blocked = client.post(route + "/commit", json=_selection(old))
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "preview_changed"
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0

    current = client.post(route + "/preview", json=_selection(old)).raise_for_status().json()
    assert current["layout_review"]["revision"] != old["layout_review"]["revision"]
    assert current["mapping"]["currency"] == "交易货币" and current["stats"]["ok"] == new_count
    assert {row["currency"] for row in current["preview"]} == {"USD"}
    stale = client.post(route + "/commit", json=_selection(old))
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "ready_evidence_stale"
    assert client.post(route + "/commit", json=_selection(current)).raise_for_status().json()["started"]
    assert poll(client, case, current["import_id"])["result"]["ok"] == new_count
    flows = client.get(base + "/flows").raise_for_status().json()["items"]
    assert len(flows) == new_count and {row["currency"] for row in flows} == {"USD"}
    [session] = database.q_raw("select request_json from import_sessions where import_id=?", [current["import_id"]])
    assert json.loads(session["request_json"])[imports.SOURCE_CURRENCY_INTERPRETATION_KEY] == imports.SOURCE_CURRENCY_INTERPRETATION_VERSION


def test_old_ready_for_an_unchanged_currency_heading_remains_confirmable(client, database, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case, base, route, ready = _upload(client, make_case, currency_heading="币种代码", first_currency="USD", first_amount="12.00")
    _remove_preview_currency_version(database, ready["import_id"])
    assert client.post(route + "/commit", json=_selection(ready)).raise_for_status().json()["started"]
    assert poll(client, case, ready["import_id"])["result"]["ok"] == 2
    assert {row["currency"] for row in client.get(base + "/flows").raise_for_status().json()["items"]} == {"USD"}


def test_old_archive_ready_checks_currency_in_every_member(client, database, make_case, monkeypatch):
    case = make_case()
    base = f"/api/v1/cases/{case}"
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        for day, name, currency in ((23, "币种", "CNY"), (24, "交易货币", "USD")):
            archive.writestr(f"{day}.csv", f"交易账号,对方账号,交易时间,借贷标志,{name},交易金额\n"
                             f"100011,100012,2040-09-{day} 13:55:10,借,{currency},12.00\n")
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    with monkeypatch.context() as previous:
        previous.setattr(table_import, "SOURCE_CURRENCY_HEADINGS", frozenset({"币种代码"}))
        old = client.post(base + "/imports/preview", files={"file": ("members.zip", raw.getvalue())}).raise_for_status().json()
    assert old["stats"]["ok"] == 2 and {row["currency"] for row in old["preview"]} == {"CNY"}
    route = base + f"/imports/{old['import_id']}"
    body = {"mode": old["mode"], "mapping": old["mapping"]}
    _remove_preview_currency_version(database, old["import_id"])
    import_preview_cache.discard(old["import_id"])
    with database._lock:
        database._con.close()
        database._con = None
    denied = client.post(route + "/commit", json=body)
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "preview_changed"
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0
    ready = client.post(route + "/preview", json=body).raise_for_status().json()
    assert {row["currency"] for row in ready["preview"]} == {"CNY", "USD"}
    confirmation = {"mode": ready["mode"], "mapping": ready["mapping"]}
    assert client.post(route + "/commit", json=confirmation).raise_for_status().json()["started"]
    assert poll(client, case, old["import_id"])["result"]["ok"] == 2
    assert {row["currency"] for row in client.get(base + "/flows").raise_for_status().json()["items"]} == {"CNY", "USD"}


def test_old_legacy_ready_does_not_commit_a_changed_currency(client, database, make_case, monkeypatch):
    case = make_case()
    raw = ("交易账号,对方账号,交易时间,借贷标志,交易货币,交易金额\n"
           "100011,100012,2040-09-23 13:55:10,借,USD,12.00\n").encode()
    with monkeypatch.context() as previous:
        previous.setattr(table_import, "SOURCE_CURRENCY_HEADINGS", frozenset({"币种代码"}))
        old = client.post("/api/import/preview", params={"case_id": case},
                          files={"file": ("source.csv", raw)}).raise_for_status().json()
    assert old["stats"]["ok"] == 1 and old["preview"][0]["currency"] == "CNY"
    body = {"case_id": case, "import_id": old["import_id"], "mode": old["mode"], "mapping": old["mapping"]}
    _remove_preview_currency_version(database, old["import_id"])
    import_preview_cache.discard(old["import_id"])
    with database._lock:
        database._con.close()
        database._con = None
    assert client.post("/api/import/commit", json=body).status_code == 409
    base = f"/api/v1/cases/{case}"
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0
    ready = client.post("/api/import/repreview", json=body).raise_for_status().json()
    assert ready["stats"]["ok"] == 1 and ready["preview"][0]["currency"] == "USD"
    assert complete(client, case, ready, legacy=True)["result"]["ok"] == 1
    assert [row["currency"] for row in client.get(base + "/flows").raise_for_status().json()["items"]] == ["USD"]


@pytest.mark.parametrize("heading", ["是否折算金额", "折算金额单位", "折合美元单位（元）", "折算后的汇率",
                                    "折算金额来源币种", "交易金额（未折算）"])
def test_conversion_metadata_is_not_a_converted_amount(heading):
    assert not table_import.is_explicit_converted_amount(heading)


@pytest.mark.parametrize("heading", ["转入转出货币金额", "转入货币金额单位", "转出货币码", "转出金额"])
def test_ambiguous_or_nonamount_directional_labels_do_not_get_the_specific_role(heading):
    assert table_import.directional_money_source_role(heading) is None
