"""Real compressed synthetic RAR3/RAR5 through the ordinary public upload."""
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import zlib

import pytest

from app import rar_archive, table_archive
from test_source_ledger import add_source
from test_source_ledger_main_flags import graph, mark, track

FIXTURES = Path(__file__).parent / "fixtures" / "native-bank"


@pytest.mark.parametrize("name", ["native-small.csv", "native-small.zip", "native-small.xls", "native-small.xlsx", "native-rar3.rar", "native-rar5.rar"])
def test_real_native_formats_preserve_source_fields_observations_and_own_node(client, make_case, monkeypatch, name):
    if name.endswith(".rar") and sys.platform not in {"darwin", "win32"}:
        pytest.skip("RAR decoder resources currently target macOS and Windows x64")
    # The product's own decoder path must work without a test-only PATH tool.
    monkeypatch.setenv("PATH", "")
    cid = make_case("SYN real format intake")
    expected = json.loads((FIXTURES / "expected.json").read_text())
    raw = (FIXTURES / name).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == expected["files"][name]
    _, job = add_source(client, cid, raw, name)
    base = f"/api/v1/cases/{cid}"
    page = client.get(base + "/flows", params={"granularity": "observations"}).json()
    assert page["total"] == expected["flow_count"]
    flows = {item["trade_no"]: item for item in page["items"]}
    assert {key: item["amount"] for key, item in flows.items()} == dict(zip(expected["primary_references"], expected["signed_amounts"]))
    assert flows["0"]["summary"] == expected["summary0"]
    assert flows["SYN-NEG"]["source_ledger"]["economic_effect"] == "unknown"
    assert flows["SYN-ZERO"]["source_ledger"]["economic_effect"] == "no_movement"
    stats = client.get(base + "/stats").json()
    assert stats["amount_total"] is None
    assert stats["source_ledger"]["count"] == expected["source_ledger_count"]
    source_id = job["result"]["source_id"]
    records = client.get(base + f"/sources/{source_id}/report").json()["items"]
    assert len(records) == expected["record_count"]
    evidence = {item["column"]: item for item in json.loads(records[0]["details"]["source_field_evidence"])}
    assert evidence["交易流水号"]["raw"] == "0" and evidence["凭证号"]["role"] == "independent_voucher_reference"
    assert evidence["备注"]["raw"] == expected["remark0"]
    assert client.get(base + f"/sources/{source_id}/file").content == raw
    mark(client, cid, expected["own_account"])
    track(client, cid, [flows["0"]])
    result = graph(client, cid)
    own, = result["nodes"]
    assert own["account"] == expected["own_account"] and own["is_main"] == 1
    assert own["out_amt"] == "12.34" and result["edges"] == []
    if name.endswith(".rar"):
        assert expected["rar_members"][name]["compressed_bytes"] < expected["rar_members"][name]["source_bytes"]
        packed = client.get(f"/api/cases/{cid}/export")
        restored = client.post("/api/cases/import", files={"file": ("rar.fundcase", packed.content)})
        assert restored.status_code == 200, restored.text
        assert client.get(f"/api/v1/cases/{restored.json()['case_id']}/flows", params={"granularity": "observations"}).json()["total"] == 4
        add_source(client, cid, raw, name)
        assert client.get(base + "/flows", params={"granularity": "observations"}).json()["total"] == 4


def unsafe_rar3():
    import rarfile
    blob = bytearray((FIXTURES / "native-rar3.rar").read_bytes())
    with rarfile.RarFile(io.BytesIO(blob), errors="strict") as archive:
        member, = archive.infolist()
        start, size = member.header_offset, member.header_size
    header = bytearray(blob[start:start + size])
    before, after = b"native-small.csv", b"../evil-file.csv"
    assert len(before) == len(after)
    index = header.index(before)
    header[index:index + len(before)] = after
    header[:2] = struct.pack("<H", zlib.crc32(header[2:]) & 0xFFFF)
    blob[start:start + size] = header
    return bytes(blob)


@pytest.mark.parametrize("failure", ["path", "count", "expanded", "decoder", "short", "crc"])
@pytest.mark.skipif(sys.platform not in {"darwin", "win32"}, reason="RAR decoder resources currently target macOS and Windows x64")
def test_rar_failure_has_no_partial_registered_import_or_case_data(client, database, make_case, monkeypatch, tmp_path, failure):
    cid = make_case("SYN RAR bounded failure")
    raw = unsafe_rar3() if failure == "path" else (FIXTURES / "native-rar5.rar").read_bytes()
    if failure == "count":
        monkeypatch.setattr(table_archive, "MAX_MEMBERS", 0)
    elif failure == "expanded":
        monkeypatch.setattr(table_archive, "MAX_EXPANDED_BYTES", 1)
    elif failure == "decoder":
        monkeypatch.setattr(rar_archive, "_decoder", lambda: tmp_path / "missing-decoder")
    elif failure in {"short", "crc"}:
        original_popen = subprocess.Popen
        output = (FIXTURES / "native-small.csv").read_bytes()
        output = output[:-1] if failure == "short" else output
        code = f"import sys;sys.stdout.buffer.write({output!r});sys.exit({0 if failure == 'short' else 2})"
        monkeypatch.setattr(rar_archive.subprocess, "Popen", lambda _command, **kwargs:
                            original_popen([sys.executable, "-c", code], **kwargs))
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("invalid.rar", raw)})
    assert response.status_code == 400, response.text
    for name in ("flows", "import_sources", "import_sessions", "batches"):
        assert database.q_raw(f"select count(*) n from {name} where case_id=?", [cid])[0]["n"] == 0
