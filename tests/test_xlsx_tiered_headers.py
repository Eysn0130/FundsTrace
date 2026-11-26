"""Public XLSX import, source coordinates, replay and package boundaries."""
import io
import json

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app import import_preview_cache, table_import, table_tiered_headers as tier
from app.main import app
from test_automatic_table_import import preview
from test_direction_recheck import DECLARED, OPPOSITE, DETAIL
from test_feedback_import_api import complete
from test_feedback_package import _archive, _members, _mutate


FLAT = ("交易日期", "付款方账号", "收款方账号", "交易金额", "币种")
UP = ("入账", "收款方", "付款方", "账面余额", "本笔发生额", "交易", "结算")
DOWN = ("年月日", "账户标识", "账户标识", "元", "分", "年月日", "币种")
ORPHAN = ("年月日", "账户标识", "账户标识", "分", "币种")
TWO_TIER_ROW = ("2026-01-20", "SYN-B", "SYN-A", "100", "301", "2026-01-07", "CNY")
ORPHAN_ROWS = (
    FLAT, ("2026-01-01", "SYN-A", "SYN-B", "10.00", "CNY"), (None,) * 5, ORPHAN,
    ("2026-01-02", "SYN-C", "SYN-D", "301", "CNY"), FLAT, ("2026-01-03", "SYN-E", "SYN-F", "20.00", "CNY"),
)
EVENT_ROWS = (
    (*FLAT, "参考号机构", "参考号渠道", "参考号业务域", "跨账簿事件参考号"),
    ("2026-02-01", "SYN-EVENT-PAY", "SYN-EVENT-REC", "43.21", "CNY", "", "", "", ""),
)


def workbook(rows, extra=()):
    book = Workbook()
    book.active.title = "SyntheticOne"
    for row in rows:
        book.active.append(row)
    if extra:
        sheet = book.create_sheet("SyntheticTwo")
        for row in extra:
            sheet.append(row)
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def ok(response):
    assert response.is_success, response.text
    return response.json()


def case_url(case):
    return f"/api/v1/cases/{case}"


def import_url(case, view):
    return f"{case_url(case)}/imports/{view['import_id']}"


def facts(rows):
    return sorted((row["trade_time"], row["payer_account"], row["payee_account"],
                   row["amount"], row["balance"], row["currency"]) for row in rows)


def journal(client, case, view):
    return {(row["member_path"], row["source_row"]): row
            for row in ok(client.get(import_url(case, view) + "/records"))["items"]}


def cold_repreview(client, case, view):
    import_preview_cache.discard()
    replay = ok(client.post(import_url(case, view) + "/preview",
                            json={"mode": view["mode"], "mapping": view["mapping"]}))
    assert replay["table_summary"] == view["table_summary"]
    assert replay["preview"] == view["preview"]
    import_preview_cache.discard()
    return replay


def test_trial_grouped_header_uses_physical_parent_and_requires_the_clock(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    book = Workbook()
    page = book.active
    page.title = "Synthetic groups"
    page.append(["双方信息", None, "交易要素", None, None, "收支发生额(分)", None, "余额信息", None])
    page.append(["本方账卡", "对方账卡", "交易日期", "时刻", "交易币种", "贷方", "借方", "账面余额", "可用余额",
                 "交易状态", "交易金额"])
    page.append(["000SYN-OUR", "000SYN-PEER", "2046-03-15", "08:01:35", "CNY", "0", "1324", "70.30", "55.30", "成功", "13.24"])
    page.append(["000SYN-OUR", "000SYN-PEER", "2046-03-15", "", "CNY", "613", "0", "76.43", "61.43", "成功", "6.13"])
    page.append(["000SYN-OUR", "000SYN-PEER", "2046-03-15", "08:03:51", "CNY", "0", "110", "70.30", "55.30", "失败", "1.10"])
    page.append(["000SYN-OUR", "000SYN-PEER", "2046-03-15", "08:05:51", "CNY", "0", "260", "67.70", "52.70", "成功", "26.00"])
    for bounds in ("A1:B1", "C1:E1", "F1:G1", "H1:I1"):
        page.merge_cells(bounds)
    data = io.BytesIO()
    book.save(data)
    book.close()
    raw = data.getvalue()
    case = make_case("Synthetic physical grouped import")
    view = preview(client, case, raw, "grouped.xlsx")
    summary = view["table_summary"]
    assert summary["profile"] == tier.GROUPED_PROFILE
    assert summary["reason_counts"] == {tier.PARENT_REASON: 1, "transaction_valid": 1, "missing_time": 1,
                                        "failed_transaction": 1, "conflicting_amount": 1}
    assert facts(view["preview"]) == [("2046-03-15 08:01:35", "000SYN-OUR", "000SYN-PEER", "13.24", "70.30", "CNY")]
    base = import_url(case, view)
    revision = view["layout_review"]["revision"]
    claim = {"member_path": "worksheets/0001", "header_row": 2, "column": 1}
    verdict = ok(client.post(base + "/ready-evidence/verify",
                             json={"revision": revision, "field": "self_account", "claims": [claim]}))
    assert verdict["evidence_status"] == "supported"
    product = ok(client.post(base + "/layout-review/model-trial", json={
        "revision": revision, "field": "self_account", "member_path": claim["member_path"], "header_row": 2,
    }))
    assert product["state"] == "rule" and product["first_calls"] == 0
    peer = next(row for row in product["packet"]["records"] if row["locator"].get("column") == 2)
    assert peer["raw"]["parent_heading"] == "双方信息" and peer["raw"]["heading"] == "对方账卡"
    job = complete(client, case, cold_repreview(client, case, view))
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    originals = journal(client, case, view)
    assert originals[("worksheets/0001", 1)]["flow_id"] is None
    assert originals[("worksheets/0001", 4)]["flow_id"] is None
    assert originals[("worksheets/0001", 5)]["flow_id"] is None
    assert originals[("worksheets/0001", 6)]["flow_id"] is None
    package = client.get(f"/api/cases/{case}/export").raise_for_status().content
    restored = ok(client.post("/api/cases/import", files={"file": ("grouped.fundcase", package)}))
    assert facts(ok(client.get(case_url(restored["case_id"]) + "/flows"))["items"]) == facts(view["preview"])


@pytest.mark.parametrize(("trade_date", "timestamp", "reason"), [
    ("2026-01-07", "2026-01-07 15:47:12", "transaction_valid"),
    ("2026-01-07 11:11:11", None, "conflicting_time"),
    (None, None, "invalid_time"),
])
def test_tiered_xlsx_explicit_clock_requires_and_checks_trade_date(client, make_case, trade_date, timestamp, reason):
    raw = workbook([(*UP, None), (*DOWN, "交易时间时分秒"),
                    (*TWO_TIER_ROW[:5], trade_date, "CNY", "15:47:12")])
    case = make_case()
    view = preview(client, case, raw, "clock.xlsx")
    summary = view["table_summary"]
    assert summary["profile"] == "funds-trace-table-v94"
    expected = [(timestamp, "SYN-A", "SYN-B", "3.01", "100.00", "CNY")] if timestamp else []
    assert facts(view["preview"]) == expected
    assert (summary["accepted"], summary["unresolved"]) == (len(expected), int(not expected))
    assert [item["reason_code"] for item in summary["unresolved_examples"]] == ([] if expected else [reason])
    job = complete(client, case, cold_repreview(client, case, view))
    assert job["stage"] == "done" and job["result"]["ok"] == len(expected), job
    assert facts(ok(client.get(case_url(case) + "/flows"))["items"]) == expected
    record = journal(client, case, view)["worksheets/0001", 3]
    assert (record["disposition"], record["reason_code"], record["flow_id"] is not None) == (
        "imported" if expected else "unresolved", reason, bool(expected))


@pytest.mark.parametrize(("swap", "bank_region"), [(False, False), (True, False), (True, True)])
def test_first_upload_rejects_orphan_with_special_formats_in_either_sheet_order(client, make_case, swap, bank_region):
    special_rows = EVENT_ROWS
    if bank_region:
        # Both special selectors can become true before scanning the orphan sheet.
        special_rows += (tuple(sorted(table_import.BANK_FEEDBACK_31_COLUMNS)),)
        control = preview(client, make_case("Synthetic special formats"), workbook(special_rows), "control.xlsx")
        assert control["table_summary"]["profile"] == "funds-trace-table-v80"
    sheets = (special_rows, ORPHAN_ROWS) if swap else (ORPHAN_ROWS, special_rows)
    case = make_case()
    url = case_url(case)
    response = client.post(url + "/imports/preview", files={"file": ("mixed.xlsx", workbook(*sheets))})
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "invalid_table_file" and "分开上传" in error["message"]
    assert ok(client.get(url + "/imports"))["items"] == []
    assert ok(client.get(url + "/flows"))["items"] == []


def test_pure_event_xlsx_keeps_v71_and_frozen_mixed_v71_replays(client, make_case, monkeypatch):
    case = make_case("Synthetic pure event workbook")
    view = preview(client, case, workbook(EVENT_ROWS), "event.xlsx")
    assert view["table_summary"]["profile"] == "funds-trace-table-v71"
    expected = ("2026-02-01 00:00:00", "SYN-EVENT-PAY", "SYN-EVENT-REC", "43.21", None, "CNY")
    assert facts(view["preview"]) == [expected]
    job = complete(client, case, cold_repreview(client, case, view))
    assert job["stage"] == "done" and job["result"]["ok"] == 1, job
    saved, = ok(client.get(case_url(case) + "/flows"))["items"]
    assert (saved["trade_time"], saved["raw_payer_account"], saved["raw_payee_account"],
            saved["amount"], saved["balance"], saved["currency"]) == expected

    historical = make_case("Synthetic frozen event workbook")
    with monkeypatch.context() as previous:
        previous.setattr(table_import, "_potential_tiers", lambda _rows: ({}, {}))
        old = preview(client, historical, workbook(ORPHAN_ROWS, EVENT_ROWS), "old.xlsx")
    assert old["table_summary"]["profile"] == "funds-trace-table-v71"
    frozen_job = complete(client, historical, cold_repreview(client, historical, old))
    assert frozen_job["stage"] == "done" and frozen_job["result"]["ok"] == 4, frozen_job
    assert {row["amount"] for row in ok(client.get(case_url(historical) + "/flows"))["items"]} == {
        "10.00", "20.00", "43.21", "301.00"}


@pytest.mark.parametrize(("rows", "version", "expected"), [
    (((*FLAT[:3], "交易金额(分)", FLAT[4]), ("2026-01-07", "SYN-A", "SYN-B", "301", "CNY")),
     "funds-trace-table-v69", [("2026-01-07 00:00:00", "SYN-A", "SYN-B", "3.01", None, "CNY")]),
    ((UP, DOWN, TWO_TIER_ROW), "funds-trace-table-v94",
     [("2026-01-07 00:00:00", "SYN-A", "SYN-B", "3.01", "100.00", "CNY")]),
    (ORPHAN_ROWS, "funds-trace-table-v94",
     [("2026-01-01 00:00:00", "SYN-A", "SYN-B", "10.00", None, "CNY"),
      ("2026-01-03 00:00:00", "SYN-E", "SYN-F", "20.00", None, "CNY")]),
])
def test_xlsx_public_three_reproductions(client, make_case, rows, version, expected):
    case = make_case()
    raw = workbook(rows)
    view = preview(client, case, raw, "synthetic.xlsx")
    assert view["table_summary"]["profile"] == version
    assert facts(view["preview"]) == expected
    job = complete(client, case, view)
    assert job["stage"] == "done" and job["result"]["ok"] == len(expected), job
    assert facts(ok(client.get(case_url(case) + "/flows"))["items"]) == expected
    assert client.get(case_url(case) + f"/sources/{view['import_id']}/file").content == raw
    records = journal(client, case, view)
    if rows == ORPHAN_ROWS:
        assert view["table_summary"]["documents"][0]["header_rows"] == [1, 4, 6]
        boundary = records["worksheets/0001", 4]
        assert json.loads(boundary["details"][tier.LEAF_PROOF_FIELD])["source_row"] == 4
        pending = records["worksheets/0001", 5]
        assert (pending["disposition"], pending["reason_code"], pending["flow_id"]) == (
            "unresolved", "ambiguous_region_header", None)
    elif version == tier.PROFILE:
        assert view["table_summary"]["documents"][0]["header_rows"] == [2]
        headers = {(item["column_index"], item["field"], item["source_role"], item["unit_factor"])
                   for item in view["table_summary"]["mapping_evidence"] if "parent_row" in item}
        assert headers == {("1", "trade_time", "posting_time", "1"), ("2", "payee_account", "payee_account", "1"),
                           ("3", "payer_account", "payer_account", "1"), ("4", "balance", "balance", "1"),
                           ("5", "amount", "amount", "0.01"), ("6", "trade_time", "transaction_time", "1")}
        assert records["worksheets/0001", 1]["reason_code"] == tier.PARENT_REASON


def test_two_tier_words_in_transactions_do_not_establish_a_boundary(client, make_case):
    rows = [
        (*FLAT, "A", "B", "C", "D"),
        ("2026-01-01", "SYN-A", "SYN-B", "25.00", "CNY", "交易", "付款方", "收款方", "本笔发生额"),
        ("2026-01-02", "SYN-A", "SYN-B", "12.00", "CNY", "年月日", "账户标识", "账户标识", "分"),
        ("2026-01-03", "SYN-C", "SYN-D", None, "CNY", "交易", "收款方", "付款方", "本笔发生额"),
        ("2026-01-04", "SYN-E", "SYN-F", None, "CNY", "年月日", "账户标识", "账户标识", "分"),
        ("2026-01-05", "SYN-G", "SYN-H", "3.50", "CNY", "2026-05-01", "SYN-X", "SYN-Y", "5000"),
    ]
    case = make_case()
    view = preview(client, case, workbook(rows), "synthetic.xlsx")
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"
    assert [row["amount"] for row in view["preview"]] == ["25.00", "12.00", "3.50"]
    assert complete(client, case, view)["stage"] == "done"
    assert [row["reason_code"] for row in journal(client, case, view).values()] == [
        "transaction_valid", "transaction_valid", "missing_amount", "missing_amount", "transaction_valid"]


def test_orphan_without_any_proved_header_keeps_original_and_no_column_roles(client, make_case):
    case = make_case()
    raw = workbook([ORPHAN, ("2026-01-02", "SYN-A", "SYN-B", "301", "CNY")])
    view = preview(client, case, raw, "only-lower.xlsx")
    assert view["table_summary"]["profile"] == "funds-trace-table-v94"
    assert view["table_summary"]["accepted"] == 0
    assert complete(client, case, view)["stage"] == "done"
    review = ok(client.get(import_url(case, view) + "/review", params={"member_path": "worksheets/0001"}))
    assert review["original_error"] is None
    assert all(row["reason_code"] == "ambiguous_region_header" and row["flow_id"] is None for row in review["items"])
    assert [(c["column"], c["value"], c["fields"]) for c in review["items"][1]["original"]][3] == (4, "301", [])


def test_mixed_direction_cold_replay_coordinates_package_and_withdrawal(database, make_case, tmp_path):
    flat = ("交易日期", "本方账号", "对方账号", "交易金额(分)", DECLARED, "币种", "余额", "说明")
    tier_up = ("入账", "对方", "本方", "账面余额", "本笔发生额", "交易", "结算", "收付")
    tier_down = (*DOWN, OPPOSITE)
    raw = workbook([
        flat, ("2026-01-01", "SYN-OWN", "SYN-PEER", "101", "X1", "CNY", "99", ""),
        tier_up, tier_down,
        ("2026-01-20", "SYN-T-PEER", "SYN-T-OWN", "200", "301", "2026-01-07", "USD", "X1"),
        ("2026-01-21", "SYN-T-PEER", "SYN-T-OWN", "220", "411", None, "USD", "X1"),
        ORPHAN, ("2026-01-22", "SYN-C", "SYN-D", "301", "902", "2026-01-08", "USD", "X1"),
        (*flat[:4], "收付标志", *flat[5:]),
        ("2026-01-09", "SYN-NO", "SYN-PEER", "501", "X1", "CNY", "3", ""),
        flat, ("2026-01-10", "SYN-AGAIN", "SYN-PEER", "606", "X1", "CNY", "9", ""),
    ], extra=[("交易", "付款方", "收款方", "本笔发生额", "账面余额"),
              ("年月日", "账户标识", "账户标识", "分", "元"),
              ("2026-01-11", "SYN-S2-PAY", "SYN-S2-REC", "705", "50")])
    expected = [
        ("2026-01-01 00:00:00", "SYN-PEER", "SYN-OWN", "1.01", "99.00", "CNY"),
        ("2026-01-07 00:00:00", "SYN-T-OWN", "SYN-T-PEER", "3.01", "200.00", "USD"),
        ("2026-01-10 00:00:00", "SYN-PEER", "SYN-AGAIN", "6.06", "9.00", "CNY"),
        ("2026-01-11 00:00:00", "SYN-S2-PAY", "SYN-S2-REC", "7.05", "50.00", "CNY"),
    ]
    case, other = make_case("Synthetic mixed workbook"), make_case("Synthetic other case")
    url = case_url(case)
    with TestClient(app) as client:
        view = preview(client, case, raw, "mixed.xlsx")
        assert view["table_summary"]["profile"] == "funds-trace-table-v94"
        assert facts(view["preview"]) == expected
    source = view["import_id"]
    route = import_url(case, view)
    request = {"mode": view["mode"], "mapping": view["mapping"]}
    import_preview_cache.discard()
    with database._lock:
        database._con.close()
        database._con = None
    with TestClient(app) as client:
        repreview = ok(client.post(route + "/preview", json=request))
        assert repreview["table_summary"] == view["table_summary"]
        assert repreview["preview"] == view["preview"]
        import_preview_cache.discard()
        job = complete(client, case, repreview)
        assert job["stage"] == "done" and job["result"]["ok"] == 4, job
        rows = ok(client.get(url + "/flows"))["items"]
        assert facts(rows) == expected
        records = journal(client, case, view)
        first, second = "worksheets/0001", "worksheets/0002"
        for position, heading_row, direction in [(2, 1, "in"), (5, 4, "out"), (12, 11, "in")]:
            direction_proof = json.loads(records[first, position]["details"][DETAIL])
            assert (direction_proof["header_row"], direction_proof["direction"]) == (heading_row, direction)
        for position, expected_reason in [(6, "unsupported_time_role"), (8, "ambiguous_region_header"), (10, "invalid_direction")]:
            record = records[first, position]
            assert (record["reason_code"], record["flow_id"]) == (expected_reason, None)
        assert DETAIL not in records[first, 8]["details"] and DETAIL not in records[first, 10]["details"]
        assert ok(client.get(url + "/stats"))["import_coverage"]["unresolved_records"] == 4

        for member, position, roles in [
            (first, 5, {2: ("SYN-T-PEER", ["counter_account"]), 3: ("SYN-T-OWN", ["self_account"]),
                        4: ("200", ["balance"]), 5: ("301", ["amount"])}),
            (second, 3, {2: ("SYN-S2-PAY", ["payer_account"]), 3: ("SYN-S2-REC", ["payee_account"]),
                         4: ("705", ["amount"]), 5: ("50", ["balance"])}),
        ]:
            review = ok(client.get(route + "/review", params={"member_path": member}))
            assert review["original_error"] is None
            cells = next(row["original"] for row in review["items"] if row["source_row"] == position)
            assert {cell["column"]: (cell["value"], cell["fields"]) for cell in cells if cell["column"] in roles} == roles
            if member == first:
                parent_cells = next(row["original"] for row in review["items"] if row["source_row"] == 3)
                assert (parent_cells[3]["header"], parent_cells[3]["fields"]) == ("账面余额", ["balance"])
        assert client.get(case_url(other) + f"/sources/{source}/file").status_code == 404
        assert client.get(import_url(other, view) + "/records").status_code == 404

        package = client.get(f"/api/cases/{case}/export")
        assert package.is_success, package.text
        restore = "/api/cases/import"
        restored = ok(client.post(restore, files={"file": ("good.fundcase", package.content)}))["case_id"]
        restored_rows = ok(client.get(case_url(restored) + "/flows"))["items"]
        assert facts(restored_rows) == expected
        restored_source = ok(client.get(f"{case_url(restored)}/flows/{restored_rows[0]['id']}/sources"))
        assert restored_source["items"][0]["source_id"] != source

        proof = json.loads(records[first, 3]["details"][tier.PROOF_FIELD])
        wrong_column = {**proof, "columns": [*proof["columns"][:-1],
                                               [8, *proof["columns"][-1][1:]]]}
        direction = json.loads(records[first, 5]["details"][DETAIL])
        digest = view["table_summary"]["members"][0]["sha256"]
        wrong_scope = tier.encode({**direction, "header_row": 1})
        wrong_direction_column = {**direction, "columns": [{**direction["columns"][0], "column_index": 7}]}
        for member, position, patch in [
            (first, 3, {tier.PROOF_FIELD: tier.encode({**proof, "parent_row": 2})}),
            (first, 3, {tier.PROOF_FIELD: tier.encode(wrong_column)}),
            (first, 5, {DETAIL: tier.encode(wrong_direction_column)}),
            (first, 5, {DETAIL: wrong_scope, tier.DIRECTION_BINDING_FIELD: tier.binding(digest, [5, wrong_scope])}),
            (second, 3, {tier.BINDING_FIELD: records[first, 5]["details"][tier.BINDING_FIELD]}),
        ]:
            members = _members(package.content)
            patch_sql = json.dumps(patch, ensure_ascii=False).replace("'", "''")
            _mutate(members, "import_records", f"update entries set details_json=json_merge_patch(details_json,'{patch_sql}') "
                    f"where member_path='{member}' and source_row={position}", tmp_path)
            case_count = database.q_raw("select count(*) n from cases")[0]["n"]
            response = client.post(restore, files={"file": ("wrong.fundcase", _archive(members))})
            assert response.status_code == 400, response.text
            assert database.q_raw("select count(*) n from cases")[0]["n"] == case_count

        anchor = next(row["id"] for row in rows if row["currency"] == "USD")
        review = ok(client.get(f"/api/context/{anchor}", params={"case_id": case, "x": 1, "review": "true"}))
        token = review["review"]["token"]
        original_route = f"/api/context/{anchor}/record/{anchor}"
        token_headers = {"X-FundsTrace-Review": token}
        original = ok(client.get(original_route, params={"case_id": case}, headers=token_headers))["originals"][0]
        assert (original["member_path"], original["source_row"]) == (first, 5)
        assert (original["cells"][4]["value"], original["cells"][4]["fields"]) == ("301", ["amount"])
        assert client.get(original_route, params={"case_id": other}, headers=token_headers).status_code == 409
        withdraw = url + f"/sources/{source}/operation"
        assert client.delete(withdraw, params={"expected_batch_id": str(job["result"]["batch_id"])}).is_success
        assert ok(client.get(url + "/flows"))["items"] == []
        assert client.get(original_route, params={"case_id": case}, headers=token_headers).status_code == 409
        assert ok(client.get(route))["result"] == job["result"]
        removed_package = client.get(f"/api/cases/{case}/export")
        assert removed_package.is_success
        assert client.post(restore, files={"file": ("withdrawn.fundcase", removed_package.content)}).is_success


def test_old_ready_and_historical_support_survive_new_orphan_assessment(client, make_case, monkeypatch):
    case = make_case()
    raw = workbook(ORPHAN_ROWS)
    with monkeypatch.context() as predecessor:
        predecessor.setattr(table_import, "_potential_tiers", lambda _rows: ({}, {}))
        old = preview(client, case, raw, "old.xlsx")
    assert old["table_summary"]["profile"] == "funds-trace-table-v69"
    import_preview_cache.discard()
    route = import_url(case, old)
    cold_old = ok(client.post(route + "/preview", json={"mode": old["mode"], "mapping": old["mapping"]}))
    assert cold_old["table_summary"] == old["table_summary"] and cold_old["table_summary"]["accepted"] == 3
    import_preview_cache.discard()
    old_job = complete(client, case, cold_old)
    assert old_job["stage"] == "done" and old_job["result"]["ok"] == 3

    new = preview(client, case, raw, "new.xlsx")
    assert new["table_summary"]["profile"] == "funds-trace-table-v94"
    new_job = complete(client, case, new)
    assert new_job["stage"] == "done" and new_job["result"]["rows_dup"] == 2
    url = case_url(case)
    assert {row["amount"] for row in ok(client.get(url + "/flows"))["items"]} == {"10.00", "20.00", "301.00"}
    coverage = ok(client.get(url + "/stats"))["import_coverage"]
    assert coverage["unresolved_records"] == 2 and not coverage["financial_interpretation_complete"]
    assert ok(client.get(route))["result"] == old_job["result"]
    # Remove the v94 duplicate support. The old, independently active source retains its flows.
    assert client.delete(url + f"/sources/{new['import_id']}/operation",
                         params={"expected_batch_id": str(new_job["result"]["batch_id"])}).is_success
    assert {row["amount"] for row in ok(client.get(url + "/flows"))["items"]} == {"10.00", "20.00", "301.00"}
