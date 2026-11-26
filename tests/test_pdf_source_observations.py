"""Source-only text PDF acceptance through the isolated product entry points."""
from hashlib import sha256
from importlib.util import find_spec
import json
import shutil

import pytest

from app import import_preview_cache, pdf_text_import, pdf_text_observations
from test_cases import archive_of, exported, import_package, members, rewrite_parquet
from test_folder_evidence import complete, table, transaction
from test_three_workflow_intake import enable, material
from test_three_workflow_intake_pdf_text import ledger_file


pytestmark = pytest.mark.skipif(not find_spec("pypdf") or not shutil.which("pdftotext"),
                                reason="Both isolated PDF text readers are required")

HEADER = ("Transaction time", "Payer", "Payee", "Amount USD", "Balance")
ROWS = [
    ("2040-01-02 08:15:11", "SYN-1", "SYN-2", "205.17", "-5.21"),
    ("2040-01-02 08:17:23", "SYN-3", "SYN-2", "3.09", "12.54"),
]
STATEMENT = [("Transaction Summary",), HEADER, *ROWS]
PAGE_HEADER = ("Date/time", *HEADER[1:])
PAGE = [("Transaction Statement",), PAGE_HEADER]
CONTINUED = [("Transaction Statement continued - Page 2",), PAGE_HEADER]
PAGE_COPY = [
    ("Transaction Statement - Page copy",),
    ("The second page repeats the first as a document copy.",),
    PAGE_HEADER,
]


def upload_pdf(client, case_id, raw, directory=False, extra_files=()):
    route = f"/api/v1/cases/{case_id}/imports"
    if directory:
        return client.post(f"{route}/directory/preview", files=[
            ("files", ("documents/anonymous.pdf", raw)), *extra_files,
        ])
    return client.post(f"{route}/preview", files={"file": ("anonymous.pdf", raw)})


def get_review(client, case, source_id, member_path):
    response = client.get(f"/api/v1/cases/{case}/imports/{source_id}/review", params={"member_path": member_path})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("directory", [False, True])
def test_pdf_source_observations_preview_commit_and_restore(client, database, make_case, monkeypatch, tmp_path, directory):
    case = make_case()
    other = make_case("Different synthetic case")
    enable(client, monkeypatch)
    raw = ledger_file(STATEMENT)
    preview_response = upload_pdf(client, case, raw, directory)
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    source = preview["import_id"]
    summary = preview["table_summary"]
    assert summary["accepted"] == 0 and summary["currency_totals"] == {}
    assert summary["reason_counts"] == {"pdf_book_scope_unproven": 2}
    assert [tuple(json.loads(row["details"]["pdf_text_original"]))
            for row in summary["unresolved_examples"]] == ROWS
    assert preview["preview"] == [] and preview["self_accounts_in_file"] == []
    if not directory:
        assert summary["profile"] == pdf_text_observations.PROFILE
    response = client.post(f"/api/v1/cases/{case}/imports/{source}/commit",
                           json={"mode": "B", "mapping": {}, "self_accounts": ["SYN-2"]})
    assert response.status_code == 422
    [item] = material(client, case, source)["items"]
    assert item["member_path"] == ("documents/anonymous.pdf" if directory else "document.pdf")
    assert (item["page"], item["role"], item["row_count"], item["accepted"], item["unresolved"]) == (
        1, "pdf_observation", 2, 0, 2)
    assert (item["status"], item["next_action"]) == ("review", "review_page")
    assert item["member_sha256"] == sha256(raw).hexdigest()
    assert client.get(item["original_url"]).content == raw
    assert client.get(item["original_url"].replace(f"/cases/{case}/", f"/cases/{other}/")).status_code == 404
    if shutil.which("pdftoppm"):
        page = client.get(f"/api/v1/cases/{case}/sources/{source}/pdf-page/1",
                          params={"member_path": item["member_path"]})
        assert page.status_code == 200 and page.content.startswith(b"\x89PNG\r\n\x1a\n")
    # A new worker and restarted process can reparse from the retained source.
    import_preview_cache.discard(source)
    result = complete(client, case, preview)
    assert result["stage"] == "done" and result["result"]["ok"] == 0, result
    [item] = material(client, case, source)["items"]
    assert item["status"] == "review"
    evidence = client.get(item["records_url"]).json()["items"]
    assert [row["details"]["pdf_row"] for row in evidence] == ["1", "2"]
    assert all(row["flow_id"] is None and row["disposition"] == "unresolved" for row in evidence)
    assert all(len(json.loads(row["details"]["pdf_text_boxes"])) == 5 for row in evidence)
    review = get_review(client, case, source, item["member_path"])
    assert review["original_error"] is None
    assert review["counts"] == {"all": 2, "review": 2, "accepted": 0}
    assert [tuple(cell["value"] for cell in row["original"]) for row in review["items"]] == [
        (*row, "USD") for row in ROWS]
    assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 0
    for name in ("account_ids", "main_accounts", "resolved_endpoints", "source_ledger_index"):
        assert not database.q_raw(f"select 1 from {name} where case_id=? limit 1", [case])
    package = exported(client, case)
    recovered = import_package(client, package)
    assert recovered.status_code == 200 and recovered.json()["flows"] == 0, recovered.text
    restored_case = recovered.json()["case_id"]
    [restored] = database.q_raw("select source_id from import_sources where case_id=?", [restored_case])
    restored_source = restored["source_id"]
    [saved] = material(client, restored_case, restored_source)["items"]
    assert (saved["page"], saved["role"], saved["unresolved"], saved["original_url"], saved["next_action"]) == (
        1, "pdf_observation", 2, None, "replace_original")
    restored_review = get_review(client, restored_case, restored_source, saved["member_path"])
    assert [row["original"] for row in restored_review["items"]] == [row["original"] for row in review["items"]]
    assert restored_review["original_error"] == "PDF 原件不可用"
    bad_package = members(package)
    rewrite_parquet(bad_package, "import_records",
                    "update rows set details_json=json_merge_patch(details_json, '{\"pdf_text_currency\":\"CNY\"}') where source_row=1",
                    tmp_path)
    assert import_package(client, archive_of(bad_package)).status_code == 400


def test_uncached_mixed_directory_does_not_drop_previewed_pdf_observations(client, make_case, monkeypatch):
    case = make_case()
    enable(client, monkeypatch)
    extra = [("files", ("data/posted.csv", table("transaction", [transaction(**{"交易金额": "42.15"})])))]
    preview = upload_pdf(client, case, ledger_file(STATEMENT), directory=True, extra_files=extra).json()
    assert preview["table_summary"]["accepted"] == 1
    source = preview["import_id"]
    import_preview_cache.discard(source)
    with monkeypatch.context() as local:
        local.setattr(pdf_text_observations, "archive_plan", lambda *_: None)
        failed = complete(client, case, preview)
        assert failed["stage"] == "error" and failed["error_code"] == "pdf_observations_changed"
    assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 0
    retry = client.post(f"/api/v1/cases/{case}/imports/{source}/preview",
                        json={"mode": preview["mode"], "mapping": preview["mapping"]})
    assert retry.status_code == 200, retry.text
    result = complete(client, case, retry.json())
    assert result["stage"] == "done" and result["result"]["ok"] == 1
    [actual] = client.get(f"/api/v1/cases/{case}/flows").json()["items"]
    assert actual["amount"] == "42.15"
    pdf_item = next(item for item in material(client, case, source)["items"] if item["role"] == "pdf_observation")
    assert get_review(client, case, source, pdf_item["member_path"])["counts"] == {
        "all": 2, "review": 2, "accepted": 0}


@pytest.mark.parametrize("variant", ["broken", "void", "hidden", "conflicting_currency", "bad_amount", "second_page"])
def test_uncertified_pdf_page_stays_out_of_the_observation_layer(client, make_case, monkeypatch, variant):
    case = make_case()
    enable(client, monkeypatch)
    monkeypatch.setenv("FUND_MINERU_EXECUTABLE", "")
    lines = STATEMENT.copy()
    if variant == "void":
        lines[0] = ("VOID - cancelled entries",)
    elif variant == "conflicting_currency":
        lines.insert(1, ("Currency: CNY",))
    elif variant == "bad_amount":
        lines[-1] = (*lines[-1][:3], "3.0.9", lines[-1][-1])
    raw = (b"broken synthetic material" if variant == "broken" else
           ledger_file(lines, hidden=variant == "hidden", extra_page=variant == "second_page"))
    response = upload_pdf(client, case, raw)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["table_summary"]["profile"] == "pdf-review-v1"
    assert preview["table_summary"]["reason_counts"] == {
        "pdf_invalid" if variant == "broken" else "pdf_tool_unavailable": 1}
    result = complete(client, case, preview)
    assert result["stage"] == "done" and result["result"]["ok"] == 0
    [item] = material(client, case, preview["import_id"])["items"]
    assert item["role"] == "pdf" and item["status"] in {"review", "unreadable"}
    assert client.get(item["original_url"]).content == raw


@pytest.mark.parametrize(("variant", "pages"), [
    ("single", [(1, 2, 2, "pdf_book_scope_unproven")]),
    ("continuation", [(1, 1, 1, "pdf_book_scope_unproven"), (2, 1, 1, "pdf_book_scope_unproven")]),
    ("copy", [(1, 1, 1, "pdf_book_scope_unproven"), (2, 1, 0, "pdf_repeated_page")]),
    ("repeat_without_declaration", [(1, 1, 1, "pdf_book_scope_unproven"), (2, 1, 1, "pdf_page_evidence_conflict")]),
    ("copy_conflict", [(1, 1, 1, "pdf_book_scope_unproven"), (2, 1, 1, "pdf_page_evidence_conflict")]),
    ("hidden_page", [(1, 1, 1, "pdf_book_scope_unproven"), (2, 1, 1, "pdf_page_evidence_conflict")]),
    ("shifted_row", [(1, 2, 2, "pdf_row_evidence_conflict")]),
])
def test_pdf_page_facts_remain_locatable_without_book_scope(
    client, database, make_case, monkeypatch, variant, pages,
):
    case = make_case()
    enable(client, monkeypatch)
    if variant == "single":
        raw = ledger_file([*PAGE, *ROWS])
    elif variant == "shifted_row":
        shifted = (ROWS[0][0], (270, ROWS[0][1]), *ROWS[0][2:])
        raw = ledger_file([*PAGE, shifted, ROWS[1]])
    elif variant == "repeat_without_declaration":
        raw = ledger_file([*PAGE, ROWS[0]], extra_page=True)
    elif variant in {"copy", "copy_conflict"}:
        raw = ledger_file([*PAGE_COPY, ROWS[0]], second_rows=[*PAGE_COPY, ROWS[variant == "copy_conflict"]])
    else:
        raw = ledger_file([*PAGE, ROWS[0]], second_rows=[*CONTINUED, ROWS[1]],
                          second_hidden=variant == "hidden_page")
    response = upload_pdf(client, case, raw)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["table_summary"]["profile"] == pdf_text_observations.PAGE_PROFILE
    finished = complete(client, case, preview)
    assert finished["stage"] == "done", finished
    assert finished["result"]["ok"] == 0
    assert finished["result"]["table_summary"]["non_transaction"] == (variant == "copy")
    source = preview["import_id"]
    intake = material(client, case, source)
    assert [(item["page"], item["row_count"], item["unresolved"], item["reason"]) for item in intake["items"]] == pages
    images = []
    for item in intake["items"]:
        assert item["role"] == "pdf_observation" and item["accepted"] == 0
        assert (item["member_sha256"], client.get(item["original_url"]).content) == (sha256(raw).hexdigest(), raw)
        if shutil.which("pdftoppm"):
            reply = client.get(f"/api/v1/cases/{case}/sources/{source}/pdf-page/{item['page']}",
                               params={"member_path": item["member_path"]})
            assert reply.status_code == 200 and reply.content.startswith(b"\x89PNG\r\n\x1a\n")
            images.append(reply.content)
    repeated_layout = variant in {"copy", "repeat_without_declaration"}
    if len(images) == 2:
        assert (images[0] == images[1]) == repeated_layout
    review = get_review(client, case, source, "document.pdf")
    expected_text = [ROWS[0], ROWS[0] if repeated_layout else ROWS[1]]
    for index, item in enumerate(review["items"]):
        detail = item["details"]
        assert item["flow_id"] is None
        assert item["disposition"] == ("non_transaction" if variant == "copy" and index else "unresolved")
        assert detail["pdf_page"] == str(index + 1 if len(pages) == 2 else 1)
        if variant == "hidden_page" and index or variant == "shifted_row" and index == 0:
            assert item["original"] is None and "pdf_text_boxes" not in detail
            if variant == "shifted_row":
                assert detail["pdf_text_line"].split()[-2:] == list(ROWS[0][-2:])
                assert len(json.loads(detail["pdf_text_line_box"])) == 4
            continue
        assert [cell["value"] for cell in item["original"]] == [*expected_text[index], "USD"]
        assert json.loads(detail["pdf_text_header"])[0] == "Date/time"
        assert json.loads(detail["pdf_text_page_size"]) == [640, 360]
        assert detail["pdf_row"] == str(1 if len(pages) == 2 else index + 1)
        boxes = json.loads(detail["pdf_text_boxes"])
        assert [box[0] for box in boxes] == [40, 210, 320, 435, 535]
        assert all(0 <= box[1] < box[3] <= 360 for box in boxes)
        if variant == "copy" and index:
            assert detail["pdf_text_copy_of_page"] == detail["pdf_text_copy_of_row"] == "1"
    assert client.get(f"/api/v1/cases/{case}/flows").json()["total"] == 0
    for name in ("account_ids", "main_accounts", "resolved_endpoints", "source_ledger_index"):
        assert not database.q_raw(f"select 1 from {name} where case_id=? limit 1", [case])


@pytest.mark.parametrize("directory", [False, True])
def test_repeated_pdf_source_has_one_observation_across_package_restore(
    client, database, make_case, monkeypatch, tmp_path, directory,
):
    case = make_case()
    enable(client, monkeypatch)
    raw = ledger_file([*PAGE_COPY, ROWS[0]], extra_page=True)
    preview = upload_pdf(client, case, raw, directory).json()
    result = complete(client, case, preview)
    assert result["stage"] == "done", result
    package = exported(client, case)
    restored = import_package(client, package)
    assert restored.status_code == 200 and restored.json()["flows"] == 0, restored.text
    restored_case = restored.json()["case_id"]
    [saved] = database.q_raw("select source_id from import_sources where case_id=?", [restored_case])
    pages = material(client, restored_case, saved["source_id"])["items"]
    assert [(item["page"], item["unresolved"], item["original_url"]) for item in pages] == [(1, 1, None), (2, 0, None)]
    review = get_review(client, restored_case, saved["source_id"], pages[0]["member_path"])
    assert review["counts"] == {"all": 2, "review": 1, "accepted": 0}
    assert review["original_error"] == "PDF 原件不可用"
    records = (
        ("details_json=json_merge_patch(details_json, '{\"pdf_page\":\"1\"}')", "source_row=2"),
        ("details_json=json_merge_patch(details_json, '{\"pdf_text_copy_of_page\":\"2\"}')", "source_row=2"),
        ("reason_code='pdf_page_evidence_conflict'", "source_row=1"),
    )
    for fields, where in records:
        damaged = members(package)
        rewrite_parquet(damaged, "import_records", f"update rows set {fields} where {where}", tmp_path)
        assert import_package(client, archive_of(damaged)).status_code == 400


def test_pdf_source_commit_requires_the_previewed_page_evidence(client, make_case, monkeypatch):
    case = make_case()
    enable(client, monkeypatch)
    raw = ledger_file([*PAGE, ROWS[0]], second_rows=[*CONTINUED, ROWS[1]])
    preview = upload_pdf(client, case, raw).json()
    original_reader = pdf_text_import.read_pages
    with monkeypatch.context() as local:
        local.setattr(pdf_text_import, "read_pages", lambda *args: [original_reader(*args)[0], None])
        disappointed = complete(client, case, preview)
        assert disappointed["stage"] == "error" and disappointed["error_code"] == "pdf_observations_changed"
    retry = client.post(f"/api/v1/cases/{case}/imports/{preview['import_id']}/preview",
                        json={"mode": "B", "mapping": {}})
    assert retry.status_code == 200, retry.text
    assert complete(client, case, retry.json())["stage"] == "done"
