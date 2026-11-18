"""Public read-only UI observations retain the existing import facts and scope."""
import io
import subprocess
import time
import zipfile
from pathlib import Path


def test_shared_import_controller_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/unified_import_harness.mjs"),
                             str(root / "tests/unified_import_host_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_committed_member_results_and_filtered_pages_are_case_bound(client, database, make_case):
    cid = make_case("Synthetic unified inspector")
    stream = io.BytesIO()
    content = ("交易时间,本方账号,交易对手账卡号,交易金额(分),交易金额(万元),收付标志,交易币种\n"
               "2026-01-01 10:00:00,000SYN-A,000SYN-B,100,0.0001,出,CNY\n"
               "2026-01-01 11:00:00,000SYN-A,000SYN-C,900,0.001,出,CNY\n").encode()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("folder/first.csv", content)
        archive.writestr("folder/second.csv", content.replace(b"000SYN-A", b"000SYN-Z"))
    uploaded = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("synthetic.zip", stream.getvalue())})
    assert uploaded.status_code == 200, uploaded.text
    view = uploaded.json(); iid = view["import_id"]
    base = f"/api/v1/cases/{cid}/imports/{iid}"
    assert client.post(base + "/commit", json={"mode": view["mode"], "mapping": view["mapping"], "mark_accounts": []}).status_code == 200
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = client.get(base).json()
        if job["stage"] in {"done", "error"}:
            break
        assert job["member_results"] == []
        time.sleep(.01)
    assert job["stage"] == "done", job
    assert job["member_results"] == [
        {"member_path": "folder/first.csv", "dispositions": {"imported": 1, "unresolved": 1}},
        {"member_path": "folder/second.csv", "dispositions": {"imported": 1, "unresolved": 1}},
    ]
    frozen_page = client.get(base + "/records", params={"member_path": "folder/second.csv", "page_size": 1}).json()
    assert frozen_page["items"][0]["preview"] is None
    page = client.get(base + "/records", params={"member_path": "folder/second.csv", "page_size": 1, "include_preview": True}).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    assert page["items"][0]["member_path"] == "folder/second.csv" and page["items"][0]["source_row"] == 2
    assert page["items"][0]["preview"]["amount"] == "1.00"
    assert page["items"][0]["preview"]["payer_account"] == "000SYN-Z"
    assert page["items"][0]["preview"]["source_member"] == "folder/second.csv"
    next_page = client.get(base + "/records", params={"member_path": "folder/second.csv", "page_size": 1, "page": 2}).json()
    assert next_page["items"][0]["disposition"] == "unresolved" and next_page["items"][0]["source_row"] == 3
    focused = client.get(base + "/records", params={"member_path": "folder/second.csv", "source_row": 3}).json()
    assert focused["total"] == 1 and len(focused["items"]) == 1
    assert focused["items"][0]["source_row"] == 3 and focused["items"][0]["record_no"] == 4
    assert focused["items"][0]["member_path"] == "folder/second.csv"
    assert client.get(base + "/records", params={"member_path": "folder/second.csv", "source_row": 128}).json()["items"] == []
    assert client.get(base + "/records", params={"source_row": 0}).status_code == 422
    assert client.get(base + "/records", params={"member_path": "x' OR 1=1 --"}).json()["items"] == []
    foreign = make_case("Synthetic foreign inspector")
    assert client.get(f"/api/v1/cases/{foreign}/imports/{iid}").status_code == 404
    assert client.get(f"/api/v1/cases/{foreign}/imports/{iid}/records", params={"member_path": "folder/second.csv"}).status_code == 404
    assert client.get(f"/api/v1/cases/{foreign}/imports/{iid}/records", params={"member_path": "folder/second.csv", "source_row": 3}).status_code == 404
    assert client.get(f"/api/v1/cases/{cid}/sources/{iid}/file").content == stream.getvalue()
    assert database.q_raw("select count(*) n from import_records where case_id=?", [cid])[0]["n"] == 4


def test_v1_file_rejection_preserves_machine_classification(client, make_case):
    cid = make_case("Synthetic bad table")
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("bad.csv", b"date,payer_account,payee_account,amount\n2026-01-01,SYN-A,SYN-B,1.01,unexpected\n")})
    assert response.status_code == 400
    assert response.headers["X-FundsTrace-Import-Failure-Kind"] == "file"


def test_generated_file_icons_are_small_real_vectors_without_embedded_bitmaps():
    import xml.etree.ElementTree as ET
    root = Path(__file__).resolve().parents[1] / "static/ui-icons"
    palettes = []
    for name in ("import-file-type-xls.svg", "import-file-type-csv.svg", "import-file-type-pdf.svg", "import-file-zip.svg", "import-file-type-txt.svg", "import-file-type-tsv.svg", "import-file-type-generic.svg"):
        raw = (root / name).read_text()
        assert len(raw.encode()) < 80_000 and "<path" in raw
        assert all(word not in raw.lower() for word in ("<image", "<script", "<foreignobject", "data:"))
        svg = ET.fromstring(raw)
        assert svg.attrib["viewBox"] and svg.attrib["width"] == "28" and svg.attrib["height"] == "28"
        palettes.append(frozenset(node.attrib.get("fill") for node in svg.iter() if node.attrib.get("fill")))
    assert len(set(palettes)) == 7
