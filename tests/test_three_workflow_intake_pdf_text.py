"""The closed PDF text ledger writes observations only after an exact-page proof."""
import hashlib
import json
from io import BytesIO
from importlib.util import find_spec
import shutil

import pytest

from app import pdf_import, pdf_text_import
from test_folder_evidence import complete
from test_three_workflow_intake import enable, material, pdf_bytes


LEDGER = [
    ("Posted Account Statement",),
    ("Account SYN-BOOK-A | Currency CNY | Opening 100.00",),
    ("Balance column is the posted book balance. Exactly two entries.",),
    ("Transaction time", "Payer", "Payee", "Amount CNY", "Book balance"),
    ("2038-02-04 09:00:01", "SYN-BOOK-A", "SYN-B", "12.34", "87.66"),
    ("2038-02-04 09:00:09", "SYN-C", "SYN-BOOK-A", "23.45", "111.11"),
]


def ledger_file(lines, *, hidden=False, extra_page=False, image_cover=False, second_rows=None, second_hidden=False):
    # An ordinary independent byte layout, with text objects positioned per field.
    def stream(rows, hide, cover=False):
        mode = "3 Tr " if hide else ""
        text = bytearray()
        for i, row in enumerate(rows):
            for x, value in zip((40, 210, 320, 435, 535), row):
                if isinstance(value, tuple):
                    x, value = value
                text.extend(f"BT /F1 12 Tf {mode}1 0 0 1 {x} {310 - 30*i} Tm ({value}) Tj ET\n".encode("ascii"))
        if cover:
            text.extend(b"q 100 0 0 20 420 188 cm /Cover Do Q\n")
        return b"<< /Length " + str(len(text)).encode() + b" >>\nstream\n" + text + b"endstream"

    resources = b"/Resources << /Font << /F1 4 0 R >>"
    resources += b" /XObject << /Cover 6 0 R >>" if image_cover else b""
    resources += b" >>"
    leaf = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 640 360] " + resources + b" /Contents 5 0 R >>"
    tree = (b"<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>" if extra_page or second_rows is not None
            else b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        tree,
        leaf,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        stream(lines, hidden, image_cover),
    ]
    if second_rows is not None:
        objects.extend([leaf.replace(b"/Contents 5 0 R", b"/Contents 7 0 R"), stream(second_rows, second_hidden)])
    elif extra_page:
        objects.append(leaf)
    if image_cover:
        objects.append(b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray "
                       b"/BitsPerComponent 8 /Length 1 >>\nstream\n\xff\nendstream")
    pdf = bytearray(b"%PDF-1.5\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj \n".encode() + obj + b"\nendobj\n")
    start = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    pdf.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]))
    pdf.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    return bytes(pdf)


@pytest.mark.skipif(not find_spec("pypdf") or not shutil.which("pdftotext"),
                    reason="Both isolated PDF readers are required for financial PDF admission")
def test_single_page_ledger_admission_source_position_retry_and_package(client, database, make_case, monkeypatch):
    case = make_case()
    enable(client, monkeypatch)
    raw = ledger_file(LEDGER)
    expected_digest = hashlib.sha256(raw).hexdigest()
    route = f"/api/v1/cases/{case}/imports/preview"
    response = client.post(route, files={"file": ("closed-layout.pdf", raw)})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["table_summary"]["profile"] == "pdf-text-ledger-v1"
    assert preview["table_summary"]["accepted"] == 2
    assert [(row["payer_account"], row["payee_account"], row["amount"], row["balance"], row["currency"])
            for row in preview["preview"]] == [
                ("SYN-BOOK-A", "SYN-B", "12.34", "87.66", "CNY"),
                ("SYN-C", "SYN-BOOK-A", "23.45", "111.11", "CNY"),
            ]
    source_id = preview["import_id"]
    [page] = material(client, case, source_id)["items"]
    assert (page["role"], page["page"], page["accepted"]) == ("transaction", 1, 2)
    assert client.get(page["original_url"]).content == raw
    assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 0
    denied = client.post(f"/api/v1/cases/{case}/imports/{source_id}/commit", json={
        "mode": "A", "mapping": {"amount": "another source cell"}})
    assert denied.status_code == 422
    receipt = complete(client, case, preview)
    assert receipt["stage"] == "done", receipt
    assert receipt["result"]["ok"] == 2
    [page] = material(client, case, source_id)["items"]
    assert page["status"] == "imported" and page["member_sha256"] == expected_digest
    rows = client.get(page["records_url"]).json()["items"]
    assert [(row["details"]["pdf_page"], row["details"]["pdf_row"], row["disposition"]) for row in rows] == [
        ("1", "1", "imported"), ("1", "2", "imported")]
    assert all(row["flow_id"] for row in rows)

    again = client.post(route, files={"file": ("same-exact-page.pdf", raw)}).json()
    repeated = complete(client, case, again)
    assert repeated["stage"] == "done", repeated
    assert (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 2)
    assert client.get(f"/api/v1/cases/{case}/flows?granularity=observations").json()["total"] == 2

    exported = client.get(f"/api/cases/{case}/export")
    recovered = client.post("/api/cases/import", files={"file": ("ledger.fundcase", exported.content)})
    assert recovered.status_code == 200, recovered.text
    restored_case = recovered.json()["case_id"]
    assert recovered.json()["flows"] == 2
    ids = database.q_raw("select source_id from import_sources where case_id=? and sha256=?",
                         [restored_case, expected_digest])
    assert len(ids) == 2
    for saved in ids:
        [item] = material(client, restored_case, saved["source_id"])["items"]
        assert (item["page"], item["original_url"], item["accepted"], item["status"]) == (1, None, 2, "imported")
        assert [tuple(json.loads(row["details"]["pdf_text_original"])) for row in
                client.get(item["records_url"]).json()["items"]] == LEDGER[4:]


@pytest.mark.parametrize("sample", ["wrong_balance", "missing_row", "one_other_currency", "duplicate_or_later_page", "hidden_overlay", "image_cover", "void_title", "ordinary_source"])
def test_pdf_text_contract_cannot_repair_missing_or_conflicting_visible_data(client, make_case, monkeypatch, sample):
    case = make_case()
    enable(client, monkeypatch)
    lines = LEDGER.copy()
    if sample == "void_title":
        lines[0] = ("VOID - cancelled entries - not executed",)
    if sample == "wrong_balance":
        lines[-1] = (*lines[-1][:-1], "111.10")
    if sample == "missing_row":
        del lines[-1]
    if sample == "one_other_currency":
        lines[1] = (lines[1][0].replace("CNY", "USD"),)
    raw = ledger_file(lines, hidden=sample == "hidden_overlay",
                      extra_page=sample == "duplicate_or_later_page", image_cover=sample == "image_cover")
    if sample == "ordinary_source":
        raw = pdf_bytes()
    assert pdf_text_import.read(BytesIO(raw), len(raw), hashlib.sha256(raw).hexdigest()) is None

    def optional_reader(_stream, _size, _digest, _cancelled):
        return {"reason": "pdf_tool_unavailable", "rows": []}

    monkeypatch.setattr(pdf_import, "extract", optional_reader)
    response = client.post(f"/api/v1/cases/{case}/imports/preview", files={"file": ("needs-review.pdf", raw)})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["table_summary"]["profile"] == "pdf-review-v1"
    result = complete(client, case, preview)
    assert result["stage"] == "done" and result["result"]["ok"] == 0
    [item] = material(client, case, preview["import_id"])["items"]
    assert client.get(item["original_url"]).content == raw
