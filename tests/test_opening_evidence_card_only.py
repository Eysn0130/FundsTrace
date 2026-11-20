"""Exact card-opening display through upload, graph and original-record APIs."""
import csv
import io
import json

import pytest
from openpyxl import Workbook

from app import event_resolution, evidence_resolution, table_import
from test_folder_evidence import archive, complete, preview as directory_preview, table


CARD = "9900123400000001"
NEAR_CARD = "9900123400000011"
AMOUNT = "12.34"
ORIGINAL_NAME = "合成流水原名"


def csv_table(headings, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headings)
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def transaction(*, card=CARD, identifier="本方卡号", trade_no="SYN-OPENING-TX", bank="合成银行"):
    return csv_table([identifier, "本方户名", "本方开户银行", "交易时间", "交易金额", "收付标志",
                      "对手账号", "对手户名", "币种", "交易流水号"],
                     [[card, ORIGINAL_NAME, bank, "2026-01-01 10:00:00", AMOUNT, "出",
                       "SYN-COUNTERPARTY", "合成对手", "CNY", trade_no]])


def opening(name="甲", *, card=CARD, full=False, bank="合成银行"):
    if full:
        return table("account", [{"交易卡号": card, "账户开户名称": name, "账号开户银行": bank}])
    return csv_table(["卡号", "户名"], [[card, name]])


def sheet_opening():
    book = Workbook()
    sheet = book.active
    sheet.title = "合成开户"
    sheet.append(["卡号", "户名"])
    sheet.append([NEAR_CARD, "相似卡开户人"])
    sheet.append([CARD, "甲"])
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


def preview(client, cid, filename, payload):
    reply = client.post(f"/api/v1/cases/{cid}/imports/preview",
                        files={"file": (filename, payload, "application/octet-stream")})
    assert reply.status_code == 200, reply.text
    return reply.json()


def submitted(client, cid, view):
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    return job["result"]


def track(client, cid):
    items = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()["items"]
    reply = client.post("/api/track", json={"case_id": cid, "flow_ids": [item["id"] for item in items]})
    assert reply.status_code == 200, reply.text
    return items


def graph(client, cid):
    reply = client.get("/api/graph", params={"case_id": cid})
    assert reply.status_code == 200, reply.text
    return reply.json()


def graph_node(client, cid, card=CARD):
    return next(node for node in graph(client, cid)["nodes"] if (node.get("display_account") or node["account"]) == card)


def assert_original_row(client, cid, locus, card=CARD, name="甲"):
    endpoint = f"/api/v1/cases/{cid}/imports/{locus['source_id']}"
    location = {"member_path": locus["member_path"], "source_row": locus["source_row"]}
    reply = client.get(endpoint + "/records", params=location)
    assert reply.status_code == 200, reply.text
    records = reply.json()
    assert records["total"] == 1
    [row] = records["items"]
    assert row["record_no"] == locus["record_no"]
    assert row["disposition"] == "auxiliary" and row["flow_id"] is None
    evidence = json.loads(row["details"]["resolution_evidence"])
    assert (evidence["role"], evidence["card"], evidence["account"], evidence["name"]) == (
        "account", card, "", name)
    review = client.get(endpoint + "/review", params={"member_path": locus["member_path"]}).json()
    original = next(record["original"] for record in review["items"] if record["source_row"] == locus["source_row"])
    return original


@pytest.mark.parametrize("variant", ["two_columns", "complete", "directory", "zip", "workbook"])
def test_opening_name_and_backlink_keep_other_observation_facts(client, make_case, variant):
    case = make_case("合成开户名回链")
    submitted(client, case, preview(client, case, "flows.csv", transaction()))
    [flow] = track(client, case)
    before = graph_node(client, case)
    assert (before["name"], flow["payer_name"], flow["amount"], flow["currency"]) == (
        ORIGINAL_NAME, ORIGINAL_NAME, AMOUNT, "CNY")
    assert "opening_holder" not in before

    if variant == "directory":
        view = directory_preview(client, case, [("nested/所给开户表.csv", opening(full=True))], scope_bank=None)
    elif variant == "zip":
        view = preview(client, case, "opening.zip", archive([("nested/所给开户表.csv", opening())]))
    elif variant == "workbook":
        view = preview(client, case, "opening.xlsx", sheet_opening())
    else:
        view = preview(client, case, "opening.csv", opening(full=variant == "complete"))
    assert view["table_summary"]["auxiliary"] == (2 if variant == "workbook" else 1)
    receipt = submitted(client, case, view)
    assert receipt["ok"] == 0
    after = graph_node(client, case)
    assert after["account"] == before["account"] and after["name"] == ORIGINAL_NAME
    holder = after["opening_holder"]
    assert (holder["status"], holder["name"]) == ("supported", "甲")
    [locus] = holder["sources"]
    assert locus["source_id"] == receipt["source_id"]
    assert locus["source_row"] == (3 if variant == "workbook" else 2)
    if variant in {"directory", "zip"}:
        assert locus["member_path"] == "nested/所给开户表.csv"
    original = assert_original_row(client, case, locus)
    original_values = {cell["header"]: cell["value"] for cell in original}
    if variant in {"complete", "directory"}:
        assert (original_values["交易卡号"], original_values["账户开户名称"]) == (CARD, "甲")
    else:
        assert (original_values["卡号"], original_values["户名"]) == (CARD, "甲")
    [current] = track(client, case)
    assert (current["id"], current["payer_account"], current["payer_name"], current["amount"]) == (
        flow["id"], flow["payer_account"], ORIGINAL_NAME, AMOUNT)
    [edge] = graph(client, case)["edges"]
    assert (edge["cnt"], edge["total"]) == (1, AMOUNT)
    assert edge["funds"]["confirmed_event_count"] == 0


def withdraw(client, cid, receipt):
    route = f"/api/v1/cases/{cid}/sources/{receipt['source_id']}/operation"
    reply = client.delete(route, params={"expected_batch_id": receipt["batch_id"]})
    assert reply.status_code == 200, reply.text


def reopen(database):
    with database._lock:
        database._con.close()
        database._con = None
        database.con()


def test_opening_conflict_withdrawal_failed_commit_and_reopen(client, database, make_case, monkeypatch):
    case = make_case("合成可撤回开户姓名")
    source_members = [("资料/流水.csv", transaction()), ("资料/开户.csv", opening())]
    first = submitted(client, case, directory_preview(client, case, source_members, scope_bank=None))
    [flow] = track(client, case)
    initial = graph_node(client, case)
    assert initial["opening_holder"]["name"] == "甲" and initial["kind"] == "card"
    location = initial["opening_holder"]["sources"][0]
    assert location["member_path"] == "资料/开户.csv"
    assert location["source_id"] == first["source_id"]
    assert_original_row(client, case, location)

    before = graph(client, case)
    before["nodes"].sort(key=lambda node: node["account"])
    late = preview(client, case, "late.csv", opening("乙"))
    def fail_after_identity_projection(*args, **kwargs):
        raise RuntimeError("synthetic failure after opening projection")
    with monkeypatch.context() as patch:
        patch.setattr(event_resolution, "rebuild", fail_after_identity_projection)
        job = complete(client, case, late)
    assert job["stage"] == "error" and job["result"] is None
    reopen(database)
    after_failure = graph(client, case)
    after_failure["nodes"].sort(key=lambda node: node["account"])
    assert after_failure == before
    assert client.get(f"/api/v1/cases/{case}/imports/{late['import_id']}/records").status_code == 404

    other = submitted(client, case, preview(client, case, "conflict.csv", opening("乙")))
    conflict = graph_node(client, case)["opening_holder"]
    assert (conflict["status"], conflict["name"]) == ("conflict", None)
    assert {entry["source_id"] for entry in conflict["sources"]} == {first["source_id"], other["source_id"]}
    withdraw(client, case, other)
    assert graph_node(client, case)["opening_holder"] == initial["opening_holder"]

    backup = submitted(client, case, preview(client, case, "retain-flow.csv", transaction()))
    assert backup["ok"] == 0 and backup["rows_dup"] == 1
    withdraw(client, case, first)
    reopen(database)
    assert "opening_holder" not in graph_node(client, case)
    observations = client.get(f"/api/v1/cases/{case}/flows", params={"granularity": "observations"}).json()["items"]
    assert [(row["id"], row["payer_name"], row["amount"]) for row in observations] == [(flow["id"], ORIGINAL_NAME, AMOUNT)]


def test_opening_scope_uses_exact_explicit_card_and_current_case(client, make_case):
    source = make_case("合成来源案")
    foreign = make_case("合成同卡异案")
    account_only = make_case("合成同值账务号")
    for case, card, identifier in ((source, NEAR_CARD, "本方卡号"), (foreign, CARD, "本方卡号"),
                                   (account_only, CARD, "本方账号")):
        submitted(client, case, preview(client, case, "tx.csv", transaction(card=card, identifier=identifier)))
        track(client, case)
    own_receipt = submitted(client, source, preview(client, source, "opening.csv", opening()))
    assert "opening_holder" not in graph_node(client, source, NEAR_CARD)
    assert "opening_holder" not in graph_node(client, foreign)
    assert client.get(f"/api/v1/cases/{foreign}/imports/{own_receipt['source_id']}/records",
                      params={"member_path": "table.csv", "source_row": 2}).status_code == 404
    submitted(client, account_only, preview(client, account_only, "opening.csv", opening()))
    assert "opening_holder" not in graph_node(client, account_only)


def test_opening_replay_migrates_derived_rows_and_package_remaps_source(client, database, make_case, monkeypatch):
    case = make_case("合成开户旧库与案件包")
    other = make_case("合成未含开户的旧库")
    submitted(client, other, preview(client, other, "other.csv", transaction()))
    submitted(client, case, preview(client, case, "flows.csv", transaction()))
    receipt = submitted(client, case, preview(client, case, "opening.csv", opening(full=True)))
    original_bytes = opening(full=True)
    track(client, case)
    initial = graph_node(client, case)["opening_holder"]
    with database.transaction():
        database.x("drop table resolution_opening_holders")
        database.x("update resolution_projection_versions set card_version=? where case_id in (?,?)",
                   ["endpoint-cards-v4-scoped-order", case, other])
    replayed, rebuild = [], evidence_resolution.rebuild
    def observe_rebuild(case_id):
        replayed.append(case_id)
        return rebuild(case_id)
    with monkeypatch.context() as patch:
        patch.setattr(evidence_resolution, "rebuild", observe_rebuild)
        reopen(database)
    assert replayed == [case]
    assert graph_node(client, case)["opening_holder"] == initial
    source = f"/api/v1/cases/{case}/sources/{receipt['source_id']}"
    assert client.get(source + "/file").content == original_bytes

    package = client.get(f"/api/cases/{case}/export")
    assert package.status_code == 200, package.text
    loaded = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert loaded.status_code == 200, loaded.text
    restored = loaded.json()["case_id"]
    track(client, restored)
    holder = graph_node(client, restored)["opening_holder"]
    assert (holder["status"], holder["name"]) == ("supported", "甲")
    [new_location] = holder["sources"]
    assert new_location["source_id"] != receipt["source_id"]
    assert (new_location["member_path"], new_location["source_row"]) == ("table.csv", 2)
    assert assert_original_row(client, restored, new_location) is None
    restored_source = f"/api/v1/cases/{restored}/sources/{new_location['source_id']}"
    assert client.get(restored_source + "/file").status_code == 404


@pytest.mark.parametrize("card", ["0001", "990012******0001"])
def test_opening_short_or_masked_card_is_retained_without_name_projection(client, make_case, card):
    case = make_case("合成不完整卡号")
    submitted(client, case, preview(client, case, "flows.csv", transaction(card=card)))
    opening_receipt = submitted(client, case, preview(client, case, "opening.csv", opening(card=card)))
    [original] = track(client, case)
    assert original["payer_name"] == ORIGINAL_NAME and original["raw_payer_account"] == card
    assert "opening_holder" not in graph_node(client, case, card)
    records = client.get(f"/api/v1/cases/{case}/imports/{opening_receipt['source_id']}/records").json()
    [row] = records["items"]
    assert json.loads(row["details"]["resolution_evidence"])["card"] == card


def test_frozen_old_parsers_do_not_adopt_new_two_column_contract():
    old = table_import.parse(opening(), suffix=".csv", profile="funds-trace-table-v69")
    assert [row["reason_code"] for row in old["records"]] == ["unknown_table", "unknown_table"]
    assert all("resolution_evidence" not in row["details"] for row in old["records"])


def test_opening_one_bank_cannot_supply_another_and_unscoped_cannot_choose(client, make_case):
    case = make_case("合成复用卡号银行边界")
    members = [("银行A.csv", transaction(trade_no="SYN-A", bank="合成银行甲")),
               ("银行B.csv", transaction(trade_no="SYN-B", bank="合成银行乙"))]
    submitted(client, case, directory_preview(client, case, members, scope_bank=None))
    track(client, case)
    submitted(client, case, preview(client, case, "unspecified.csv", opening("丙")))
    assert not any("opening_holder" in node for node in graph(client, case)["nodes"])
    submitted(client, case, preview(client, case, "bank-a.csv", opening("甲", full=True, bank="合成银行甲")))
    cards = {node["bank"]: node for node in graph(client, case)["nodes"] if node.get("kind") == "card"}
    assert cards["合成银行甲"]["opening_holder"]["name"] == "甲"
    assert "opening_holder" not in cards["合成银行乙"]


def test_repeated_opening_keeps_current_backlink_when_one_batch_is_withdrawn(client, make_case):
    case = make_case("合成重复开户支持")
    submitted(client, case, preview(client, case, "flows.csv", transaction()))
    track(client, case)
    by_source = {}
    for filename in ("first.csv", "second.csv"):
        receipt = submitted(client, case, preview(client, case, filename, opening()))
        by_source[receipt["source_id"]] = receipt
    [locus] = graph_node(client, case)["opening_holder"]["sources"]
    withdraw(client, case, by_source.pop(locus["source_id"]))
    remaining = graph_node(client, case)["opening_holder"]
    assert remaining["name"] == "甲"
    assert {source["source_id"] for source in remaining["sources"]} == set(by_source)
