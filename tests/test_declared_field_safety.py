"""Public source declarations must not silently become precise wrong values."""
import csv
import io
import time
import zipfile
from decimal import Decimal

from app import table_import, table_archive


def encoded(headers, row):
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows([headers, row])
    return stream.getvalue().encode("utf-8-sig")


def zipped(payload):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("transactions.csv", payload)
    return stream.getvalue()


def test_public_unknown_clock_and_conflicting_unit_keep_sources_without_wrong_values(client, make_case):
    payloads = [
        (encoded(["交易日期(YYYYMMDD)", "交易时刻(HHmmssfffffff)", "本方账号", "对方账号", "收付标志", "交易金额"],
                 ["20260101", "100000123456", "SYN-A", "SYN-B", "出", "12.34"]), "invalid_time"),
        (encoded(["交易时间", "本方账号", "对方账号", "收付标志", "交易金额(元)", "金额单位"],
                 ["2026-01-01 10:00:00.123456", "SYN-A", "SYN-B", "出", "1234", "分"]), "conflicting_unit"),
    ]
    for index, (payload, reason) in enumerate(payloads):
        for entry in ("csv", "zip", "directory"):
            cid = make_case(f"SYN declaration {index} {entry}")
            suffix = ".csv" if entry == "csv" else ".zip"
            content = payload if entry == "csv" else zipped(payload)
            base = f"/api/v1/cases/{cid}/imports"
            response = client.post(base + ("/directory/preview" if entry == "directory" else "/preview"),
                                   files=[("files" if entry == "directory" else "file", ("source" + suffix, content))])
            assert response.status_code == 200, response.text
            view = response.json()
            assert client.post(base + f"/{view['import_id']}/commit", json={
                "mode": view["mode"], "mapping": view["mapping"], "mark_accounts": []}).status_code == 200
            deadline = time.monotonic() + 10
            while True:
                job = client.get(base + f"/{view['import_id']}").json()
                if job["stage"] in {"done", "error"}:
                    break
                assert time.monotonic() < deadline
                time.sleep(.005)
            assert job["stage"] == "done", job
            assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0
            records = client.get(base + f"/{view['import_id']}/records").json()["items"]
            assert len(records) == 1 and records[0]["source_row"] == 2
            assert records[0]["disposition"] == "unresolved"
            assert reason in records[0]["reason_code"] + records[0]["details"].get("validation_issues", "")
            original = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content
            if entry == "directory":
                with zipfile.ZipFile(io.BytesIO(original)) as archive:
                    assert archive.read("source.zip") == content
            else:
                assert original == content
            assert client.delete(f"/api/batches/{job['result']['batch_id']}", params={"case_id": cid}).status_code == 200
            assert client.get(f"/api/v1/cases/{cid}/flows").json()["total"] == 0


def test_declaration_profiles_preserve_historical_replay_and_supported_clock():
    unknown = encoded(["交易日期(YYYYMMDD)", "交易时刻(HHmmssfffffff)", "本方账号", "对方账号", "收付标志", "交易金额"],
                      ["20260101", "100000123456", "SYN-A", "SYN-B", "出", "12.34"])
    historical = table_import.parse(unknown, suffix=".csv", profile=table_import.CURRENCY_REGION_PROFILE)
    assert historical["rows"][0]["trade_time"].hour == 0
    current = table_import.parse(unknown, suffix=".csv")
    assert current["profile"] == table_import.SOURCE_OBSERVATION_PROFILE and current["rows"] == []
    known = encoded(["交易日期(YYYYMMDD)", "交易时刻(HHmmss)", "本方账号", "对方账号", "收付标志", "交易金额"],
                    ["20260101", "100000", "SYN-A", "SYN-B", "出", "12.34"])
    assert table_import.parse(known, suffix=".csv")["rows"][0]["trade_time"].hour == 10
    conflicting = encoded(["交易时间", "本方账号", "对方账号", "收付标志", "交易金额(元)", "金额单位"],
                          ["2026-01-01 10:00:00", "SYN-A", "SYN-B", "出", "1234", "分"])
    historical_zip = table_archive.parse(io.BytesIO(zipped(conflicting)), profile=table_archive.HEADER_PROFILE)
    assert historical_zip["rows"][0]["amount"] == Decimal("1234")
    current_zip = table_archive.parse(io.BytesIO(zipped(conflicting)))
    assert current_zip["profile"] == table_archive.SOURCE_OBSERVATION_PROFILE and current_zip["rows"] == []
