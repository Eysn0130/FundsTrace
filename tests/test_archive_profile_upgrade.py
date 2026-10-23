"""Synthetic public upgrades keep frozen archive rules and existing flow facts."""
import csv
import datetime as dt
from decimal import Decimal
import io
import json
import zipfile

from fastapi import HTTPException
import pytest

from app import case_packages, table_archive, table_import
from app.services import imports
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_feedback_package import _archive, _members, _mutate


COLUMNS = tuple("交易卡号 交易账号 查询反馈结果原因 交易户名 交易证件号码 交易时间 交易金额 交易余额 收付标志 交易对手账卡号 现金标志 对手户名 对手身份证号 对手开户银行 摘要说明 交易币种 交易网点名称 交易发生地 交易是否成功 传票号 IP地址 MAC地址 对手交易余额 交易流水号 日志号 凭证种类 凭证号 交易柜员号 备注 商户名称 商户代码 交易类型".split())


def transaction(number="SYN-MAIN", voucher="SYN-VOUCHER", amount="1.01", **changes):
    row = dict.fromkeys(COLUMNS, "")
    row.update({"交易账号": "SYN-OWN", "交易户名": "Synthetic owner",
                "交易时间": "2026-01-01 10:00:00", "交易金额": amount,
                "交易余额": "100.00", "收付标志": "出", "交易对手账卡号": "SYN-COUNTER",
                "对手户名": "Synthetic counterparty", "交易币种": "CNY", "交易是否成功": "1",
                "交易流水号": number, "凭证号": voucher})
    row.update(changes)
    return row


def source(rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(COLUMNS)
    writer.writerows([[row[column] for column in COLUMNS] for row in rows])
    payload = stream.getvalue().encode("gb18030")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("table.csv", payload)
    return payload, archive.getvalue()


def frozen_preview(client, case_id, content, monkeypatch, profile=table_archive.IDENTITY_PROFILE):
    # Recreate a historical preview using its frozen parser. Its saved pin must
    # outlive the process default used by the subsequent public commit.
    original = imports._parse

    def historical(*args, **options):
        options["table_profile"] = profile
        return original(*args, **options)

    with monkeypatch.context() as context:
        context.setattr(imports, "_parse", historical)
        return preview(client, case_id, content, "synthetic.zip")


def report(client, case_id, view):
    response = client.get(f"/api/v1/cases/{case_id}/sources/{view['import_id']}/report")
    assert response.status_code == 200, response.text
    return response.json()


def test_historical_ready_archive_keeps_v1_after_default_upgrade(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic frozen archive")
    _, content = source([transaction()])
    view = frozen_preview(client, cid, content, monkeypatch)
    assert view["table_summary"]["profile"] == table_archive.IDENTITY_PROFILE
    assert view["table_summary"]["unresolved"] == 1
    base = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    revised = client.post(base + "/preview", json={"mode": view["mode"], "mapping": view["mapping"]})
    assert revised.status_code == 200, revised.text
    assert revised.json()["table_summary"]["profile"] == table_archive.IDENTITY_PROFILE
    job = complete(client, cid, revised.json())
    assert job["stage"] == "done" and job["result"]["ok"] == 0
    assert job["result"]["table_summary"]["profile"] == table_archive.IDENTITY_PROFILE
    assert database.q("select parser_version from import_sources where case_id=?", [cid]) == [
        {"parser_version": table_archive.IDENTITY_PROFILE}]
    assert report(client, cid, view)["items"][0]["details"].keys() <= table_import.DETAILS_FIELDS


def test_v2_adds_only_prior_unresolved_and_repeated_v1_cannot_restore_old_coverage(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic archive semantic upgrade")
    _, content = source([transaction(), transaction("SYN-KEEP", "SYN-KEEP", "2.02"),
                         transaction("", "SYN-FALLBACK", "3.03"), transaction("", "", "4.04")])
    old = frozen_preview(client, cid, content, monkeypatch)
    first = complete(client, cid, old)
    assert first["stage"] == "done" and first["result"]["ok"] == 3
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]["unresolved_records"] == 1

    current = preview(client, cid, content, "synthetic.zip")
    assert current["table_summary"]["profile"] == table_archive.PROFILE
    upgraded = complete(client, cid, current)
    assert upgraded["stage"] == "done", upgraded
    assert (upgraded["result"]["ok"], upgraded["result"]["rows_dup"]) == (1, 3)
    after = database.q_raw("select * from flows where case_id=? order by id", [cid])
    assert after[:3] == before
    assert after[3]["trade_no"] == "SYN-MAIN"
    assert sum(row["amount"] for row in after) == Decimal("10.10")
    notes = [record["details"]["trade_no_basis"] for record in report(client, cid, current)["items"]]
    assert notes == ["source_transaction_id", "source_transaction_id", "legacy_voucher_fallback", "none"]
    assert [(r["disposition"], r["flow_id"]) for r in report(client, cid, old)["items"]] == [
        ("unresolved", None), *(("imported", str(row["id"])) for row in before)]

    for view, expected_duplicates in ((preview(client, cid, content, "repeat.zip"), 4),
                                      (frozen_preview(client, cid, content, monkeypatch), 3)):
        repeated = complete(client, cid, view)
        assert repeated["stage"] == "done" and repeated["result"]["ok"] == 0
        assert repeated["result"]["rows_dup"] == expected_duplicates
    coverage = client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]
    assert coverage["unresolved_records"] == 0 and coverage["financial_interpretation_complete"]
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == after


def test_ordinary_csv_upgrades_by_same_physical_key_without_readding_accepted_facts(client, database, make_case):
    cid = make_case("Synthetic CSV archive promotion")
    payload, content = source([transaction("SYN-KEEP", "SYN-KEEP", "2.02"), transaction()])
    ordinary = complete(client, cid, preview(client, cid, payload))
    assert ordinary["stage"] == "done" and ordinary["result"]["ok"] == 1
    original = database.q_raw("select * from flows where case_id=?", [cid])[0]
    upgraded = complete(client, cid, preview(client, cid, content, "synthetic.zip"))
    assert upgraded["stage"] == "done", upgraded
    assert (upgraded["result"]["ok"], upgraded["result"]["rows_dup"]) == (1, 1)
    assert database.q_raw("select * from flows where id=?", [original["id"]]) == [original]
    assert database.q_raw("select count(*) n,sum(amount) amount from flows where case_id=?", [cid]) == [
        {"n": 2, "amount": Decimal("3.03")}]
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]["unresolved_records"] == 0


def test_unknown_future_evaluation_still_precedes_latest_supported_v2(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic future archive coverage")
    _, content = source([transaction()])
    historical = frozen_preview(client, cid, content, monkeypatch)
    assert complete(client, cid, historical)["stage"] == "done"
    assert complete(client, cid, preview(client, cid, content, "synthetic.zip"))["stage"] == "done"
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]["unresolved_records"] == 0
    # An older application cannot interpret a future assessment, even when a
    # later supported assessment says the physical record was imported.
    database.x("update import_sources set parser_version='funds-trace-table-archive-v99' where source_id=?",
               [historical["import_id"]])
    assert complete(client, cid, preview(client, cid, content, "latest.zip"))["result"]["ok"] == 0
    coverage = client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]
    assert coverage["unresolved_records"] == 1 and not coverage["financial_interpretation_complete"]


def test_envelope_reuse_prefers_v2_then_v1_then_ordinary_with_matching_facts(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic archive reuse preference")
    payload, content = source([transaction("SYN-KEEP", "SYN-KEEP")])
    ordinary = preview(client, cid, payload)
    complete(client, cid, ordinary)
    historical = frozen_preview(client, cid, content, monkeypatch)
    complete(client, cid, historical)
    current = preview(client, cid, content, "synthetic.zip")
    complete(client, cid, current)
    fields = "case_id,batch_id," + ",".join((*table_import.FLOW_FIELDS, "self_account", "self_name"))
    original = database.q("select id from flows where case_id=?", [cid])[0]["id"]
    # Create independent identical prior candidates to exercise precedence;
    # ordinary imports themselves correctly fold these into one flow.
    for view in (historical, current):
        flow_id = database.q(f"insert into flows({fields}) select {fields} from flows where id=? returning id",
                             [original])[0]["id"]
        database.x("update flow_sources set flow_id=? where source_id=?", [flow_id, view["import_id"]])
    preferred = flow_id
    repeated = frozen_preview(client, cid, content, monkeypatch)
    job = complete(client, cid, repeated)
    assert job["stage"] == "done" and (job["result"]["ok"], job["result"]["rows_dup"]) == (0, 1)
    assert report(client, cid, repeated)["items"][0]["flow_id"] == str(preferred)
    # The physical key cannot override a changed own-account fact.
    database.x("update flows set self_account='SYN-CHANGED' where case_id=?", [cid])
    fresh = preview(client, cid, content, "fresh.zip")
    assert complete(client, cid, fresh)["result"]["ok"] == 1


def test_mixed_v1_v2_package_keeps_versions_notes_and_no_originals(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic mixed archive package")
    _, content = source([transaction(), transaction("SYN-KEEP", "SYN-KEEP", "2.02")])
    complete(client, cid, frozen_preview(client, cid, content, monkeypatch))
    complete(client, cid, preview(client, cid, content, "synthetic.zip"))
    package = case_packages.export_package(cid)[0]
    members = _members(package)
    assert json.loads(members["meta.json"])["format"] == 4
    assert json.loads(members["meta.json"])["source_files"] == "not_included"
    assert not any(path.endswith((".csv", ".zip")) for path in members)
    restored = case_packages.import_package(package)["case_id"]
    assert database.q("select parser_version from import_sources where case_id=? order by parser_version", [restored]) == [
        {"parser_version": table_archive.IDENTITY_PROFILE}, {"parser_version": table_archive.PROFILE}]
    fields = ",".join(table_import.FLOW_FIELDS)
    assert database.q_raw(f"select {fields} from flows where case_id=? order by trade_no", [restored]) == database.q_raw(
        f"select {fields} from flows where case_id=? order by trade_no", [cid])
    assert database.q_raw("select details_json from import_records where case_id=? order by details_json", [restored]) == database.q_raw(
        "select details_json from import_records where case_id=? order by details_json", [cid])


@pytest.mark.parametrize("profile,filename", [(table_archive.IDENTITY_PROFILE, "synthetic.zip"),
                                             (table_import.PROFILE, "synthetic.csv"),
                                             (table_archive.PROFILE, "synthetic.zip")])
def test_package_rejects_new_notes_disguised_as_old_or_unknown_basis(client, database, make_case, tmp_path, profile, filename):
    cid = make_case("Synthetic archive metadata rejection")
    _, content = source([transaction("SYN-KEEP", "SYN-KEEP")])
    complete(client, cid, preview(client, cid, content, "synthetic.zip"))
    members = _members(case_packages.export_package(cid)[0])
    basis = "guessed" if profile == table_archive.PROFILE else "source_transaction_id"
    _mutate(members, "import_sources", f"update entries set parser_version='{profile}',filename='{filename}'", tmp_path)
    _mutate(members, "import_records", "update entries set details_json=json_merge_patch(details_json,'"
            + json.dumps({"trade_no_basis": basis}) + "')", tmp_path)
    before = database.q("select id from cases order by id")
    with pytest.raises(HTTPException):
        case_packages.import_package(_archive(members))
    assert database.q("select id from cases order by id") == before


@pytest.mark.parametrize("profile", [table_archive.IDENTITY_PROFILE, table_archive.PROFILE])
def test_archive_failure_rolls_back_and_unresolved_original_remains_protected(client, database, make_case, monkeypatch, profile):
    cid = make_case("Synthetic archive rollback and retention")
    _, content = source([transaction(), transaction("SYN-KEEP", "SYN-KEEP", "-2.02")])
    view = frozen_preview(client, cid, content, monkeypatch, profile)
    original = database.insert_dataframe

    def fail_after_records(table, frame, columns):
        result = original(table, frame, columns)
        if table == "import_records":
            raise RuntimeError("Synthetic journal rollback")
        return result

    with monkeypatch.context() as failure:
        failure.setattr(database, "insert_dataframe", fail_after_records)
        assert complete(client, cid, view)["stage"] == "error"
    for table in ("flows", "batches", "import_sources", "flow_sources", "import_members", "import_records"):
        assert database.q(f"select count(*) n from {table} where case_id=?", [cid]) == [{"n": 0}]
    assert complete(client, cid, view)["stage"] == "done"
    database.x("update import_sessions set expires_at=? where case_id=?", [dt.datetime(2020, 1, 1), cid])
    result = client.post(f"/api/v1/cases/{cid}/imports/cleanup", json={"dry_run": False})
    assert result.status_code == 200 and result.json()["protected"] == 1 and result.json()["deleted"] == 0
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == content


def test_unknown_explicit_archive_pin_is_rejected_without_fallback(client, database, make_case):
    cid = make_case("Synthetic unknown frozen rule")
    _, content = source([transaction()])
    view = preview(client, cid, content, "synthetic.zip")
    database.x("update import_sessions set request_json=? where import_id=?", [
        json.dumps({"_table_profile": "funds-trace-table-archive-v99"}), view["import_id"]])
    base = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    body = {"mode": view["mode"], "mapping": view["mapping"]}
    assert client.post(base + "/preview", json=body).status_code == 400
    assert client.post(base + "/commit", json=body).status_code == 400
    assert database.q("select count(*) n from import_sources where case_id=?", [cid]) == [{"n": 0}]
    with pytest.raises(table_import.TableImportError):
        table_archive.parse(io.BytesIO(content), profile="funds-trace-table-archive-v99")
