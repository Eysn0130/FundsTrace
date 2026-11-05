"""A complete native family and a populated voucher type delimit reference roles."""
import csv
import io
import json

import pytest

from app import feedback_import, table_import
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


@pytest.mark.parametrize("amount,count,disposition", [("0.00", 0, "non_monetary"), ("12.34", 1, "imported")])
def test_public_typed_native_voucher_is_independent_of_transaction_serial(client, make_case, amount, count, disposition):
    cid = make_case("Synthetic typed native voucher")
    data = source(amount=amount)
    view = preview(client, cid, data)
    assert view["table_summary"]["unresolved"] == 0
    assert complete(client, cid, view)["stage"] == "done"
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()
    assert rows["total"] == 1 and rows["funds"]["observation_count"] == count
    if not count:
        assert rows["items"][0]["amount"] == "0.00"
        assert rows["items"][0]["source_ledger"]["economic_effect"] == "no_movement"
    if count:
        assert rows["items"][0]["trade_no"] == "SYN-PRIMARY"
    record = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"][0]
    assert record["disposition"] == disposition
    evidence = json.loads(record["details"]["source_field_evidence"])
    assert {(item["role"], item["raw"]) for item in evidence} == {
        ("source_transaction_id", "SYN-PRIMARY"), ("independent_voucher_reference", "SYN-INDEPENDENT-VOUCHER"), ("independent_voucher_type", "SYN-VOUCHER-TYPE"),
        ("source_slip_reference", ""), ("source_log_reference", ""), ("summary", ""), ("remark", "")}
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == data


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
