"""Verify rule-prepared desktop uploads through the normal commit and source paths."""
import hashlib
from io import BytesIO

import pytest
from openpyxl import Workbook

from test_api_v1 import poll


def upload(client, case, header, rows):
    raw = (header + "\n" + "\n".join(rows) + "\n").encode()
    base = f"/api/v1/cases/{case}/imports"
    response = client.post(base + "/preview", files={"file": ("synthetic.csv", raw)})
    assert response.status_code == 200, response.text
    view = response.json()
    return view, base + "/" + view["import_id"], raw


def commit_body(view):
    return {"mode": view["mode"], "mapping": view["mapping"], "mark_accounts": [],
            **({"ready_revision": view["layout_review"]["revision"]} if view["layout_applied"] else {})}


@pytest.mark.parametrize(("clock_heading", "expected_time"), [
    ("交易时间", "2026-11-07 12:34:56"),
    ("记账时间", "2026-11-07 00:00:00"),
])
def test_trial_prepares_explicit_side_alias_and_checks_split_time(client, make_case, monkeypatch, clock_heading, expected_time):
    header = f"交易日期,{clock_heading},本端账号,对方账号,收付标志,交易金额,交易币种,证件号码"
    rows = ["2026-11-07,12:34:56,000SYN-A,000SYN-B,出,20.35,CNY,TEST-ID"]
    normal = make_case("Normal attached table")
    old, _, _ = upload(client, normal, header, rows)
    assert old["table_summary"]["accepted"] == 0 and not old["layout_applied"]

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    trial = make_case("Desktop trial with split times")
    value, base, raw = upload(client, trial, header, rows)
    assert value["automatic"] and value["layout_applied"] and not value["layout_model_trial"]
    assert value["layout_rule_evidence"] == {
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "regions": [{"member_path": "table.csv", "header_row": 1, "last_row": 2, "column": 3,
                     "basis": "scoped_own_account_alias", "note": None}],
    }
    assert value["table_summary"]["accepted"] == 1
    assert client.get(f"/api/v1/cases/{trial}/flows").json()["total"] == 0
    response = client.post(base + "/commit", json=commit_body(value))
    assert response.status_code == 200, response.text
    assert poll(client, trial, value["import_id"])["stage"] == "done"
    [flow] = client.get(f"/api/v1/cases/{trial}/flows?granularity=observations").json()["items"]
    assert (flow["payer_account"], flow["payee_account"], flow["trade_time"], flow["amount"]) == (
        "000SYN-A", "000SYN-B", expected_time, "20.35")
    source = client.get(f"/api/v1/cases/{trial}/sources/{value['import_id']}/file")
    assert hashlib.sha256(source.content).digest() == hashlib.sha256(raw).digest()
    [record] = client.get(base + "/records").json()["items"]
    assert record["source_row"] == 2 and record["flow_id"] == flow["id"]


@pytest.mark.parametrize("purpose", ["历史档案查阅", "档案查询"])
def test_trial_prepares_complete_role_note_without_model_configuration(client, make_case, monkeypatch, purpose):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    workbook = (f"字段口径：本方甲账号只用于{purpose}；本方乙账号是逐笔交易本方账号。\n"
                "交易时间,本方甲账号,本方乙账号,对方账号,收付标志,交易金额,交易币种")
    value, base, _ = upload(client, case, workbook, [
        "2026-11-07 12:34:56,SYN-OLD,SYN-CURRENT,SYN-OTHER,入,20.35,CNY",
    ])
    assert value["automatic"] and value["layout_applied"] and not value["layout_model_trial"]
    [evidence] = value["layout_rule_evidence"]["regions"]
    assert evidence["column"] == 3 and evidence["basis"] == "source_role_statement"
    assert evidence["note"] == {"member_path": "table.csv", "source_row": 1}
    assert value["mapping"]["self_account"] == "本方乙账号"
    response = client.post(base + "/commit", json=commit_body(value))
    assert response.status_code == 200, response.text
    assert poll(client, case, value["import_id"])["stage"] == "done"
    [flow] = client.get(f"/api/v1/cases/{case}/flows").json()["items"]
    assert (flow["payer_account"], flow["payee_account"]) == ("SYN-OTHER", "SYN-CURRENT")


def test_note_support_for_one_column_does_not_discard_another_account_column(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    header = ("字段口径：甲账户是交易本方账号。\n"
              "交易时间,甲账户,乙账户,对方账号,收付标志,交易金额,交易币种")
    value, _, _ = upload(client, make_case(), header, [
        "2026-11-07 12:34:56,SYN-A,SYN-OTHER-POSSIBLE,SYN-B,出,20.35,CNY",
    ])
    assert value["layout_model_trial"] and not value["layout_applied"]


@pytest.mark.parametrize("extra, bad_row", [
    (",附加账号", ",SYN-OTHER-LEDGER"),
    ("", ""),
])
def test_trial_leaves_competing_accounts_or_bad_tail_row_unprepared(client, make_case, monkeypatch, extra, bad_row):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    header = "交易时间,本端账号,对方账号,收付标志,交易金额,交易币种" + extra
    rows = ["2026-11-07 12:34:56,SYN-A,SYN-B,出,10.25,CNY" + bad_row for _ in range(9)]
    if not extra:
        rows[-1] = rows[-1].replace(",出,", ",未确定,")
    value, _, _ = upload(client, case, header, rows)
    assert value["layout_model_trial"] and not value["layout_applied"]
    assert value["table_summary"]["accepted"] == 0


def test_trial_blocks_ordinary_registered_mapping_when_another_own_alias_competes(client, make_case, monkeypatch):
    header = "交易时间,本方账号,本端账号,对方账号,收付标志,交易金额,交易币种"
    rows = ["2026-11-07 12:34:56,SYN-A,SYN-ALSO-OWN,SYN-B,出,10.25,CNY"]
    normal, _, _ = upload(client, make_case("Normal registered"), header, rows)
    assert normal["automatic"] and not normal["layout_model_trial_conflict"]

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Unresolved competing own identities")
    value, base, _ = upload(client, case, header, rows)
    assert value["automatic"] and value["layout_model_trial"] and value["layout_model_trial_conflict"]
    attempted = client.post(base + "/commit", json=commit_body(value))
    assert attempted.status_code == 409 and attempted.json()["error"]["code"] == "trial_source_role_conflict"
    assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 0


@pytest.mark.parametrize(("heading", "note_rows"), [("本端账号", 33), ("交易账号", 5)])
def test_trial_does_not_commit_before_relevant_field_note_coverage_is_verified(client, make_case, monkeypatch, heading, note_rows):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    book = Workbook()
    book.active.title = "流水"
    book.active.append(["交易时间", heading, "对方账号", "收付标志", "交易金额", "交易币种"])
    book.active.append(["2026-11-07 12:34:56", "SYN-QUERY", "SYN-OTHER", "出", "20.35", "CNY"])
    notes = book.create_sheet("字段说明")
    notes.append(["字段", "字段说明"])
    for _ in range(note_rows - 2):
        notes.append(["流水!D1 收付标志", "方向"])
    notes.append([f"流水!B1 {heading}", "查询条件"])
    content = BytesIO()
    book.save(content)
    base = f"/api/v1/cases/{case}"
    uploaded = client.post(base + "/imports/preview", files={"file": ("scoped-notes.xlsx", content.getvalue())})
    assert uploaded.status_code == 200, uploaded.text
    view = uploaded.json()
    assert not view["layout_applied"]
    attempted = client.post(f'{base}/imports/{view["import_id"]}/commit', json=commit_body(view))
    assert attempted.status_code in (409, 422), attempted.text
    assert client.get(base + "/flows").json()["total"] == 0


@pytest.mark.parametrize("registered, alternate", [("本方账号", "本端账号"), ("本端账号", "本方账号")])
def test_trial_cannot_hide_competing_own_account_in_later_columns(client, make_case, monkeypatch, registered, alternate):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    padding = [f"栏目{index}" for index in range(20)]
    header = ",".join(["交易时间", registered, "对方账号", "收付标志", "交易金额", "交易币种", *padding, alternate])
    row = ",".join(["2026-11-07 12:34:56", "SYN-A", "SYN-B", "出", "10.25", "CNY", *([""] * 20), "SYN-OTHER"])
    value, base, _ = upload(client, case, header, [row])
    assert value["layout_model_trial"] and value["layout_model_trial_conflict"]
    report = client.post(base + "/layout-review/model-trial", json={
        "revision": value["layout_review"]["revision"], "member_path": "table.csv",
        "header_row": 1, "field": "self_account",
    }).json()
    assert (report["state"], report["reason"], report["first_calls"]) == ("pending", "source_role_conflict", 0)
    response = client.post(base + "/commit", json=commit_body(value))
    assert response.status_code == 409 and response.json()["error"]["code"] == "trial_source_role_conflict"


@pytest.mark.parametrize("scope", ["本期流水!A1:G3", "前期流水!A1:G3"])
def test_trial_only_applies_narrative_roles_proved_for_the_whole_current_region(client, make_case, monkeypatch, scope):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    book = Workbook()
    book.active.title = "本期流水"
    book.active.append(["交易时间", "甲栏目", "乙栏目", "对方账号", "收付标志", "交易金额", "交易币种"])
    book.active.append(["2026-10-07 12:00:00", "000SYN-A", "000SYN-A", "SYN-B", "出", "20.35", "CNY"])
    book.active.append(["2026-10-07 12:01:00", "000SYN-A", "000SYN-C", "SYN-B", "入", "10.00", "CNY"])
    notes = book.create_sheet("栏位释义")
    notes.append(["适用区域", "字段", "说明原文"])
    notes.append([scope, "甲栏目", "该列只存放提交查询时使用的登记编号。"])
    notes.append([scope, "乙栏目", "该列为每笔交易的本方账簿账号，不表示对方账户。"])
    data = BytesIO()
    book.save(data)
    root = f"/api/v1/cases/{case}"
    upload = client.post(root + "/imports/preview", files={"file": ("synthetic-roles.xlsx", data.getvalue())})
    assert upload.status_code == 200, upload.text
    view = upload.json()
    layout = view["layout_review"]
    member = next(item["member_path"] for item in layout["members"] if item["sheet"] == "本期流水")
    base = f'{root}/imports/{view["import_id"]}'
    response = client.post(base + "/layout-review/model-trial", json={
        "revision": layout["revision"], "member_path": member, "header_row": 1,
        "field": "self_account", "prepare_layout": True,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["first_calls"] == result["recheck_calls"] == 0
    assert client.get(root + "/flows").json()["total"] == 0
    if scope.startswith("前期流水"):
        assert result["state"] == "pending" and result["prepared_preview"] is None
        return

    assert result["state"] == "rule"
    prepared = result["prepared_preview"]
    assert prepared["layout_rule_evidence"]["regions"][0]["column"] == 3
    assert prepared["layout_rule_evidence"]["regions"][0]["note"]["source_row"] == 3
    assert client.post(base + "/commit", json=commit_body(prepared)).status_code == 200
    job = poll(client, case, view["import_id"])
    assert job["stage"] == "done"
    assert job["result"]["layout_decision"]["origin"] == "rule"
    flows = client.get(root + "/flows?granularity=observations").json()["items"]
    assert {(row["payer_account"], row["payee_account"], row["amount"]) for row in flows} == {
        ("000SYN-A", "SYN-B", "20.35"), ("SYN-B", "000SYN-C", "10.00")}


@pytest.mark.parametrize(("caption", "prefix", "exception", "accepted"), [
    ("对账单", "仅适用于本表区下列逐笔流水", "", True),
    ("材料说明：以下栏位用途仅由本页说明确定。", "仅适用于本表区下列逐笔流水", "", True),
    ("对账单", "仅适用于另一本账", "", False),
    ("对账单", "仅适用于本表区下列逐笔流水", "；最后一行另按旧口径", False),
    ("字段说明：最后一行的本方账户在查询位置。", "仅适用于本表区下列逐笔流水", "", False),
    ("以下示例禁止入账：以下栏位用途仅由本页说明确定。", "仅适用于本表区下列逐笔流水", "", False),
])
def test_same_sheet_role_note_covers_the_complete_physical_region(client, make_case, monkeypatch, caption, prefix,
                                                               exception, accepted):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    book = Workbook()
    sheet = book.active
    sheet.append([caption])
    sheet.append([f"{prefix}：过账位置是每笔款项实际入账或支出的本方账户；查询位置是检索编号，不是逐笔本方账号{exception}。"])
    sheet.append(["交易时间", "过账位置", "查询位置", "对方账号", "收付标志", "交易金额", "交易币种"])
    sheet.append(["2026-11-07 12:34:56", "000SYN-Z", "000SYN-Z", "SYN-B", "入", "20.35", "CNY"])
    sheet.append(["2026-11-07 12:35:56", "000SYN-A", "000SYN-Z", "SYN-B", "出", "10.00", "CNY"])
    data = BytesIO()
    book.save(data)

    base = f"/api/v1/cases/{case}"
    response = client.post(base + "/imports/preview", files={"file": ("synthetic-inline.xlsx", data.getvalue())})
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["layout_applied"] is accepted
    if not accepted:
        assert view["table_summary"]["accepted"] == 0
        return
    [region] = view["layout_rule_evidence"]["regions"]
    assert (region["header_row"], region["last_row"], region["column"]) == (3, 5, 2)
    assert region["note"]["source_row"] == 2
    source = base + f'/imports/{view["import_id"]}'
    assert client.post(source + "/commit", json=commit_body(view)).status_code == 200
    job = poll(client, case, view["import_id"])
    assert job["stage"] == "done" and job["result"]["layout_decision"]["origin"] == "rule"
    flows = client.get(base + "/flows?granularity=observations").json()["items"]
    assert {(row["payer_account"], row["payee_account"], row["amount"]) for row in flows} == {
        ("SYN-B", "000SYN-Z", "20.35"), ("000SYN-A", "SYN-B", "10.00")}
