"""Monetary precision must survive parsing, deduplication and delivered Excel."""
from decimal import Decimal
import io

import pandas as pd
import pytest
import duckdb
from app import importer


def test_decimal_cents_survive_import_and_deduplication(database, make_case, flow_row):
    cid = make_case("Exact decimal cents")
    first = importer.parse_amount("9999999999999999.99")
    second = importer.parse_amount("9999999999999999.98")
    assert first == Decimal("9999999999999999.99")
    assert second == Decimal("9999999999999999.98")
    rows = [flow_row(amount=first), flow_row(amount=second)]
    assert importer.commit_rows("synthetic.csv", "A", rows, case_id=cid)["ok"] == 2
    assert importer.commit_rows("synthetic.csv", "A", rows, case_id=cid)["rows_dup"] == 2
    with database._lock:
        amounts = database.con().execute("select amount from flows where case_id=? order by amount", [cid]).fetchall()
    assert amounts == [(Decimal("9999999999999999.98"),), (Decimal("9999999999999999.99"),)]


def test_exported_excel_keeps_large_amount_and_identifier_as_text(client, database, make_case, flow_row):
    cid = make_case("Exact Excel")
    amount = Decimal("9999999999999999.99")
    importer.commit_rows("synthetic.csv", "A", [flow_row(amount=amount)], case_id=cid)
    fid = database.q("select id from flows where case_id=?", [cid])[0]["id"]
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [fid]}).status_code == 200
    response = client.get("/api/export/tracked.xlsx", params={"case_id": cid})
    assert response.status_code == 200
    frame = pd.read_excel(io.BytesIO(response.content), sheet_name="追踪流水", dtype=str)
    assert frame.iloc[0]["交易金额"] == "9999999999999999.99"
    assert frame.iloc[0]["流水ID"] == str(fid)


def test_default_currency_is_deduplicated_using_the_persisted_currency(database, make_case, flow_row):
    cid = make_case("Default currency repeat")
    rows = [flow_row(currency=None), flow_row(currency="CNY")]
    first = importer.commit_rows("synthetic.csv", "A", rows, case_id=cid)
    assert first["ok"] == 1
    second = importer.commit_rows("synthetic.csv", "A", rows, case_id=cid)
    assert second["ok"] == 0
    assert second["rows_dup"] == 2


@pytest.mark.parametrize("field", ["amount", "balance"])
def test_late_large_decimal_does_not_depend_on_dataframe_type_samples(database, make_case, flow_row, field):
    cid = make_case(f"Unsampled decimal {field}")
    # At the default 1,000-row sample, the last value in 2,000 rows is missed.
    rows = [flow_row(trade_no=f"SYN-SAMPLE-{index}", amount=Decimal("1.00"), balance=Decimal("1.00")) for index in range(2000)]
    rows[-1][field] = Decimal("9999999999999999.98")
    result = importer.commit_rows("synthetic-late-decimal.csv", "A", rows, case_id=cid)
    assert result["ok"] == 2000
    assert database.q_raw(f"select {field} from flows where case_id=? and trade_no=?", [cid, "SYN-SAMPLE-1999"])[0][field] == Decimal("9999999999999999.98")
    assert importer.commit_rows("synthetic-repeat.csv", "A", rows, case_id=cid)["rows_dup"] == 2000


@pytest.mark.parametrize("field", ["amount", "balance"])
def test_amount_outside_storage_range_rejects_the_whole_batch(database, make_case, flow_row, field):
    cid = make_case(f"Decimal range {field}")
    invalid = flow_row(trade_no="SYN-OUT-OF-RANGE")
    invalid[field] = Decimal("10000000000000000.00")
    with pytest.raises(duckdb.Error):
        importer.commit_rows("synthetic-overflow.csv", "A", [flow_row(), invalid], case_id=cid)
    assert database.q("select count(*) n from flows where case_id=?", [cid]) == [{"n": 0}]
    assert database.q("select count(*) n from batches where case_id=?", [cid]) == [{"n": 0}]
