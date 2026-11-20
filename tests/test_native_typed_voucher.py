"""A complete native family and a populated voucher type delimit reference roles."""
import csv
import io
import json

import pytest

from app import feedback_import, import_preview_cache, table_import
from app.services import imports
from test_automatic_table_import import preview
from test_feedback_import_api import complete


def source(*, amount="0.00", kind="SYN-VOUCHER-TYPE", extra=(), omit=None):
    headers = [name for name in feedback_import.COLUMNS if name != omit]
    values = {"交易账号": "SYN-NATIVE-A", "交易时间": "2026-02-03 10:00:00", "交易金额": amount,
              "交易余额": "100.00", "收付标志": "进", "交易对手账卡号": "SYN-NATIVE-B",
              "交易币种": "CNY", "交易流水号": "SYN-PRIMARY", "凭证种类": kind,
              "凭证号": "SYN-INDEPENDENT-VOUCHER", "交易是否成功": "01"}
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows([[*headers, *[name for name, _ in extra]],
        [*[values.get(name, "") for name in headers], *[value for _, value in extra]]])
    return stream.getvalue().encode("utf-8-sig")


@pytest.mark.parametrize("frozen_profile", [None, "funds-trace-table-v69"], ids=["current", "frozen-v69"])
@pytest.mark.parametrize("amount,count,disposition", [("0.00", 0, "non_monetary"), ("12.34", 1, "imported")])
def test_public_typed_native_voucher_is_independent_of_transaction_serial(
        client, make_case, monkeypatch, frozen_profile, amount, count, disposition):
    cid = make_case("Synthetic typed native voucher")
    data = source(amount=amount)
    if frozen_profile:
        parse = imports._parse

        def parse_frozen(*args, **options):
            return parse(*args, **{**options, "table_profile": frozen_profile, "_auto_event_profile": False})

        with monkeypatch.context() as historical:
            historical.setattr(imports, "_parse", parse_frozen)
            view = preview(client, cid, data)
        import_preview_cache.discard(view["import_id"])
    else:
        view = preview(client, cid, data)
    profile = frozen_profile or "funds-trace-table-v81"
    assert view["table_summary"]["profile"] == profile
    assert view["table_summary"]["unresolved"] == 0
    job = complete(client, cid, view)
    assert job["stage"] == "done"
    assert job["result"]["table_summary"]["profile"] == profile

    base = f"/api/v1/cases/{cid}"
    rows = client.get(base + "/flows").json()
    assert rows["total"] == 1 and rows["funds"]["observation_count"] == count
    flow, = rows["items"]
    assert (flow["trade_no"], flow["amount"]) == ("SYN-PRIMARY", amount)
    if not count:
        assert flow["source_ledger"]["economic_effect"] == "no_movement"

    source_id = view["import_id"]
    record, = client.get(f"{base}/imports/{source_id}/records").json()["items"]
    assert (record["disposition"], record["flow_id"], record["source_row"]) == (disposition, flow["id"], 2)
    field_evidence = record["details"]["source_field_evidence"]
    evidence = json.loads(field_evidence)
    expected = {
        ("source_transaction_id", "交易流水号", "SYN-PRIMARY"),
        ("independent_voucher_reference", "凭证号", "SYN-INDEPENDENT-VOUCHER"),
        ("independent_voucher_type", "凭证种类", "SYN-VOUCHER-TYPE"),
        ("source_slip_reference", "传票号", ""), ("source_log_reference", "日志号", ""),
        ("summary", "摘要说明", ""), ("remark", "备注", ""),
    }
    if frozen_profile is None:
        expected.update({("amount", "交易金额", amount), ("balance", "交易余额", "100.00"),
                         ("currency", "交易币种", "CNY")})
    assert len(evidence) == len(expected)
    assert {(item["role"], item["column"], item["raw"]) for item in evidence} == expected

    links = client.get(f"{base}/flows/{flow['id']}/sources").json()
    assert links["total"] == 1
    link, = links["items"]
    assert (link["source_id"], link["source_row"], link["member_path"], link["member_row"]) == (
        source_id, record["record_no"], "table.csv", 2)
    assert (link["observation_flow_id"], link["observation_amount"], link["observation_balance"],
            link["observation_currency"]) == (flow["id"], amount, "100.00", "CNY")
    assert link["parser_version"] == profile and link["source_available"]
    assert link["record_details"]["source_field_evidence"] == field_evidence
    assert client.get(f"{base}/sources/{source_id}/file").content == data


@pytest.mark.parametrize("extra", [(('交易流水号', 'SYN-CONFLICT'),), (('凭证号', 'SYN-CONFLICT'),)])
def test_native_typed_voucher_keeps_same_role_conflict(client, make_case, extra):
    cid = make_case("Synthetic native same-role conflict")
    view = preview(client, cid, source(amount="12.34", extra=extra))
    assert view["table_summary"]["accepted"] == 0 and view["table_summary"]["unresolved"] == 1
    assert complete(client, cid, view)["stage"] == "done"


@pytest.mark.parametrize("change", [{"kind": ""}, {"omit": "交易柜员号"}])
def test_native_voucher_requires_complete_schema_and_positive_type(change):
    # v54 required a positive voucher type. Current complete native28 inputs
    # deliberately keep independent identifiers even when this type is blank.
    result = table_import.parse(source(amount="12.34", **change), suffix=".csv", profile=table_import.AUXILIARY_SCOPE_PROFILE)
    assert not result["rows"] and result["records"][0]["reason_code"] == "conflicting_field"


def test_previous_profile_keeps_native_voucher_ambiguity():
    result = table_import.parse(source(), suffix=".csv", profile=table_import.OPTIONAL_REFERENCE_PROFILE)
    assert not result["rows"] and result["records"][0]["reason_code"] == "conflicting_field"
