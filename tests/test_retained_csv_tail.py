"""Normal API custody of CSVs with physically absent final cells."""
import csv
import io

import pytest

from test_automatic_table_import import preview
from test_feedback_import_api import complete


def source(width=37, *, variant=None):
    fields = ["交易账号", "交易日期", "交易金额", "余额", "交易摘要", "借贷标志", "币种", "现转标志"]
    head = [*fields, *(f"来源字段 {index}" for index in range(9, width - 1)), "", ""]
    common = ["000001002345", "2039-04-05", "17.23", "120.97", "memo", "贷", "CNY", "转账"]
    data = [common + [""] * (width - len(common)) for _ in range(4)]
    data[2][5] = "借"
    data[-1] = data[-1][:-2]
    if variant == "middle_short":
        data[1].pop()
    elif variant == "nonblank_suffix":
        data[0][-1] = "reference belongs to this physical column"
    elif variant == "typed_shift":
        # Remove an interior value and its delimiter instead of trailing cells.
        data[-1] = [*data[1][:2], *data[1][4:]]
    elif variant == "wide_record":
        data[1].append("unexpected extra physical data")
    elif variant == "new_header":
        data[2] = head.copy()
    elif variant == "known_lost_role":
        head[-1] = "交易流水号"
    elif variant == "too_many_rows":
        data = [*[data[0]] * 256, data[-1]]
    elif variant == "no_tail":
        data[-1].extend(("", ""))
    elif variant == "conflicting_direction_marker":
        data[-1][5] = "借/收"
    cells = [head, *data]
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(cells)
    return stream.getvalue().encode(), cells


def enable_trial(client, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)


@pytest.mark.parametrize("width", [21, 37])
def test_final_missing_csv_cells_retained_and_read_by_physical_position(client, database, make_case, monkeypatch, width):
    enable_trial(client, monkeypatch)
    case = make_case()
    raw, original = source(width)
    view = preview(client, case, raw)
    assert view["automatic"] is False and not view.get("layout_model_trial", False)
    assert view["table_summary"]["profile"] == "funds-trace-table-v103"
    assert view["table_summary"]["accepted"] == 0 and view["table_summary"]["unresolved"] == 4
    assert view["mapping"] == {}
    base = f"/api/v1/cases/{case}"
    job = f"{base}/imports/{view['import_id']}"
    source_path = f"{base}/sources/{view['import_id']}"
    layout = view["layout_review"]
    [member] = layout["members"]
    assert member["source_rows"] == 5 and member["header_rows"] == [1]
    received = {}
    for first in range(1, width + 1, 16):
        piece = client.post(job + "/layout-review", json={"revision": layout["revision"],
            "member_path": "table.csv", "row_start": 1, "column_start": first}).raise_for_status().json()
        for row in piece["rows"]:
            received.setdefault(row["source_row"], []).extend((cell["column"], cell["value"]) for cell in row["cells"])
    assert received == {number: list(enumerate(row, 1)) for number, row in enumerate(original, 1)}
    material = client.get(job + "/intake").raise_for_status().json()
    assert material["source_available"]
    notes = material["source_field_notes"]
    assert any(note["columns"] == [1, 3, 6] and note["rows"] == [2, 3, 5] and "本方入账" in note["text"] for note in notes)
    assert any(note["columns"] == [1, 3, 6] and note["rows"] == [4] and "本方出账" in note["text"] for note in notes)
    assert any(note["columns"] == [3, 4] and note["rows"] == [2, 3, 4, 5] and "两列不可互代" in note["text"] for note in notes)

    bad_mapping = {"amount": "交易金额"}
    assert client.post(job + "/preview", json={"mode": "B", "mapping": bad_mapping}).status_code in (409, 422)
    assert client.post(job + "/commit", json={"mode": "B", "mapping": bad_mapping}).status_code in (409, 422)
    assert client.post(job + "/commit", json={"mode": "B", "mapping": {}, "mark_accounts": ["000001002345"]}).status_code == 422
    physical_mapping = {"revision": layout["revision"], "confirmed": True, "regions": [{"member_path": "table.csv", "header_row": 1,
        "last_row": 5, "mode": "B", "mapping": {"self_account": 1, "trade_time": 2, "amount": 3,
            "direction": 6, "currency": 7}}]}
    forbidden = client.post(job + "/layout-review/apply", json=physical_mapping)
    assert forbidden.status_code == 422 and forbidden.json()["error"]["code"] == "csv_tail_source_only"
    model = client.post(job + "/layout-review/model-trial", json={"revision": layout["revision"],
        "member_path": "table.csv", "header_row": 1, "field": "own_direction_amount"})
    assert model.status_code == 422 and model.json()["error"]["code"] == "csv_tail_source_only"
    repeated = client.post(job + "/preview", json={"mode": "B", "mapping": {}}).raise_for_status().json()
    assert repeated["table_summary"]["profile"] == view["table_summary"]["profile"]
    assert repeated["table_summary"]["reason_counts"] == view["table_summary"]["reason_counts"]
    finished = complete(client, case, repeated)
    assert finished["stage"] == "done" and finished["result"]["ok"] == 0
    with database._lock:
        database._con.close()
        database._con = None
    cold = client.get(job + "/review", params={"member_path": "table.csv", "view": "all", "page_size": 10}).raise_for_status().json()
    assert len(cold["items"]) == 4
    for row in cold["items"]:
        assert row["disposition"] == "unresolved" and row["flow_id"] is None
        assert [(item["column"], item["value"]) for item in row["original"]] == list(enumerate(original[row["source_row"] - 1], 1))
    assert client.get(job + "/intake").raise_for_status().json()["source_field_notes"] == notes
    assert client.get(source_path + "/file").content == raw
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0
    withdrawn = client.delete(source_path + "/operation", params={"expected_batch_id": finished["result"]["batch_id"]})
    assert withdrawn.status_code == 200
    assert client.get(job + "/intake").raise_for_status().json()["stage"] == "withdrawn"


@pytest.mark.parametrize("variant", ["middle_short", "nonblank_suffix", "typed_shift", "wide_record", "new_header",
                                    "known_lost_role", "too_many_rows", "bad_quotes", "wrong_separator"])
def test_suspect_ragged_csv_is_not_admitted_by_tail_source_route(client, make_case, monkeypatch, variant):
    enable_trial(client, monkeypatch)
    case = make_case()
    raw, _ = source(variant=variant)
    if variant == "bad_quotes":
        raw = raw[:-2] + b'"unterminated'
    elif variant == "wrong_separator":
        head, body = raw.split(b"\r\n", 1)
        raw = head.replace(b",", b"\t") + b"\r\n" + body
    base = f"/api/v1/cases/{case}"
    result = client.post(base + "/imports/preview", files={"file": ("synthetic.csv", raw)})
    assert result.status_code == 400
    assert result.json()["error"]["code"] == "invalid_table_file"
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


def test_tail_retention_does_not_change_the_default_or_take_over_a_strictly_valid_csv(client, make_case, monkeypatch):
    case = make_case()
    raw, _ = source()
    base = f"/api/v1/cases/{case}"
    bad = client.post(base + "/imports/preview", files={"file": ("synthetic.csv", raw)})
    assert bad.status_code == 400
    enable_trial(client, monkeypatch)
    full, _ = source(variant="no_tail")
    valid = preview(client, case, full)
    assert valid["table_summary"]["profile"] != "funds-trace-table-v103"
    assert valid["table_summary"]["accepted"] == 4


def test_tail_can_retain_originals_without_appointing_an_unclear_direction(client, make_case, monkeypatch):
    enable_trial(client, monkeypatch)
    case = make_case()
    raw, _ = source(variant="conflicting_direction_marker")
    view = preview(client, case, raw)
    assert view["table_summary"]["reason_counts"] == {"csv_tail_source_only": 4}
    job = f"/api/v1/cases/{case}/imports/{view['import_id']}"
    assert complete(client, case, view)["result"]["ok"] == 0
    result = client.get(job + "/intake").raise_for_status().json()
    assert result["source_available"]
    assert [(item["status"], item["unresolved"]) for item in result["items"]] == [("review", 4)]
    notes = result.get("source_field_notes", ())
    assert all("本方入账" not in note["text"] and "本方出账" not in note["text"] for note in notes)
    assert {r["source_row"] for r in client.get(job + "/records").raise_for_status().json()["items"]} == {2, 3, 4, 5}


@pytest.mark.parametrize(("table", "change", "reason"), [
    ("import_records", "update rows set disposition='non_monetary', reason_code='zero_amount' where source_row=2",
     "只能恢复原位置的来源记录"),
    ("import_members", "update rows set encoding='xlsx' where member_path='table.csv'", "来源成员与已登记范围不一致"),
    ("import_members", "update rows set size_bytes=size_bytes-1 where member_path='table.csv'", "来源成员与已登记范围不一致"),
], ids=["disposition", "encoding", "size"])
def test_tail_source_package_keeps_only_the_registered_journal(
        client, database, make_case, monkeypatch, tmp_path, table, change, reason):
    from test_cases import archive_of, exported, import_package, members, rewrite_parquet

    enable_trial(client, monkeypatch)
    native = make_case()
    view = preview(client, native, source()[0])
    assert complete(client, native, view)["result"]["ok"] == 0
    bundle = exported(client, native)
    restored = import_package(client, bundle).raise_for_status().json()["case_id"]
    [saved] = database.q_raw("select source_id, parser_version from import_sources where case_id=?", [restored])
    assert saved["parser_version"] == "funds-trace-table-v103"
    base = f"/api/v1/cases/{restored}"
    intake = client.get(f"{base}/imports/{saved['source_id']}/intake").raise_for_status().json()
    assert not intake["source_available"]
    [item] = intake["items"]
    assert item["reason"] == "csv_tail_source_only" and item["accepted"] == 0 and item["unresolved"] == 4
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0

    changed = members(bundle)
    rewrite_parquet(changed, table, change, tmp_path)
    counts = """select (select count(*) from cases) cases, (select count(*) from import_sources) sources,
                (select count(*) from import_records) records, (select count(*) from flows) flows"""
    before = database.q_raw(counts)
    tampered = import_package(client, archive_of(changed))
    assert tampered.status_code == 400 and reason in tampered.json()["detail"]
    assert database.q_raw(counts) == before


def test_tail_source_profile_is_not_selected_for_a_directory_member(client, make_case, monkeypatch):
    from test_folder_evidence import preview as folder_preview

    enable_trial(client, monkeypatch)
    case = make_case()
    view = folder_preview(client, case, [("partial.csv", source()[0])], scope_bank=None)
    assert view["table_summary"]["profile"].startswith("funds-trace-table-archive-")
    assert view["table_summary"]["accepted"] == 0
    assert "csv_tail_source_only" not in view["table_summary"]["reason_counts"]


def optional_metadata_source(*, tail=("交易IP地址", "MAC"), last_variant=None):
    heading = ["交易时间", "本方账号", "借贷标志", "交易金额", "币种", "交易流水号", "对方账号", *tail]
    last = ["2042-03-04 10:01:13", "0000010", "借", "17.05", "CNY", "000-03", "0000023"]
    if last_variant == "shifted":
        # A middle deletion plus one trailing deletion yields the same
        # width as a terminal omission and shifts the payable account.
        last = [*last[:5], last[6], ""]
    elif last_variant == "shifted_network":
        # A present IP slid into the last financial column after the
        # account delimiter was lost. The shorter record is still nonempty.
        last[6] = "192.0.2.30"
    elif last_variant == "bad_currency":
        last[4] = "UNSUPPORTED"
    records = [heading,
               ["2042-03-04 09:00:00", "0000010", "贷", "1.00", "CNY", "000-01", "0000021", *[""] * len(tail)],
               ["2042-03-04 09:30:00", "0000010", "借", "2.25", "CNY", "000-02", "0000022", *[""] * len(tail)],
               last]
    buf = io.StringIO(newline="")
    csv.writer(buf, delimiter="\t").writerows(records)
    return buf.getvalue().encode(), records


def test_optional_metadata_terminal_suffix_can_post_without_padding(client, database, make_case, monkeypatch):
    from app import table_import

    enable_trial(client, monkeypatch)
    case = make_case("Synthetic optional metadata terminal omission")
    raw, cells = optional_metadata_source()
    ready = preview(client, case, raw)
    assert ready["table_summary"]["profile"] == table_import.CSV_TAIL_METADATA_PROFILE
    assert ready["stats"]["accepted"] == 3 and ready["automatic"] is False
    root = f"/api/v1/cases/{case}"
    job = root + f"/imports/{ready['import_id']}"
    base = {"mode":ready["mode"],"mapping":ready["mapping"],"mark_accounts":[]}
    assert client.post(job + "/commit", json=base).status_code == 409
    body = {**base, "ready_revision":ready["layout_review"]["revision"]}
    assert client.post(job + "/preview", json={**body, "mapping":{"amount":"对方账号"}}).status_code == 422
    assert client.post(job + "/commit", json=body).status_code == 200
    from test_api_v1 import poll
    result = poll(client, case, ready["import_id"])["result"]
    assert result and result["ok"] == 3
    with database._lock:
        database._con.close()
        database._con = None
    original = client.get(job+"/review",params={"member_path":"table.csv","view":"all"}).raise_for_status().json()["items"]
    assert [[cell["value"] for cell in item["original"]] for item in original] == cells[1:]
    active = client.get(root+"/flows",params={"granularity":"observations"}).raise_for_status().json()["items"]
    assert {(row["trade_no"],row["amount"],row["raw_payer_account"],row["raw_payee_account"]) for row in active} == {
        ("000-01","1.00","0000021","0000010"),("000-02","2.25","0000010","0000022"),
        ("000-03","17.05","0000010","0000023")}
    backup=client.get(f"/api/cases/{case}/export").raise_for_status().content
    restored=client.post("/api/cases/import", files={"file":("metadata-tail.fundcase",backup)}).raise_for_status().json()
    assert client.get(f"/api/v1/cases/{restored['case_id']}/flows").raise_for_status().json()["total"]==3
    saved = root+f"/sources/{ready['import_id']}"
    assert client.get(saved+"/file").content==raw
    assert client.delete(saved+"/operation",params={"expected_batch_id":result["batch_id"]}).status_code==200
    assert client.get(root+"/flows").raise_for_status().json()["total"]==0
    assert client.get(saved+"/file").content==raw


@pytest.mark.parametrize(("tail", "variant", "keep"),[
    (("IP地址","MAC地址"),"shifted",0),
    (("IP地址","MAC地址"),"shifted_network",0),
    (("交易发生地",""),"bad_currency",2),
])
def test_optional_tail_keeps_unknown_metadata_shifts_and_bad_currency_pending(client, make_case, monkeypatch, tail, variant, keep):
    from app import table_import

    enable_trial(client, monkeypatch)
    case = make_case("Synthetic optional metadata guard")
    raw, _ = optional_metadata_source(tail=tail,last_variant=variant)
    view = preview(client, case, raw)
    if keep == 0:
        assert view["table_summary"]["profile"]==table_import.CSV_TAIL_SOURCE_PROFILE
    else:
        assert view["table_summary"]["profile"]==table_import.CSV_TAIL_METADATA_PROFILE
        assert view["table_summary"]["reason_counts"]["invalid_currency"]==1
    assert view["stats"]["accepted"]==keep


@pytest.mark.parametrize("generic_counterparty", [False, True])
@pytest.mark.parametrize(("party", "direction", "payer", "payee"), [
    ("收款人", "出", "000010", "000021"),
    ("付款人", "进", "000021", "000010"),
])
def test_optional_tail_preserves_named_party_on_the_verified_side(
        client, database, make_case, monkeypatch, party, direction, payer, payee, generic_counterparty):
    from app import table_import
    from test_api_v1 import poll

    enable_trial(client, monkeypatch)
    case = make_case()
    rows = [
        ["交易时间", "本方账号", "收付标志", "交易金额", "交易流水号", "对方账号",
         party + "账号", party + "名称", "交易IP地址", "MAC"],
        ["2042-03-04 09:00:00", "000010", direction, "8.40", "TX-A", "",
         "000021", "Synthetic party A", "", ""],
        ["2042-03-04 09:30:00", "000010", direction, "11.25", "TX-B", "000022",
         "000022", "Synthetic party B", "", ""],
        ["2042-03-04 10:30:00", "000010", direction, "17.05", "TX-C", "000023",
         "000099", "Synthetic party C"],
    ]
    if not generic_counterparty:
        for row in rows:
            del row[5]
    content = io.StringIO(newline="")
    csv.writer(content, delimiter="\t").writerows(rows)
    raw = content.getvalue().encode()
    ready = preview(client, case, raw)
    assert ready["table_summary"]["profile"] == table_import.CSV_TAIL_METADATA_PROFILE
    assert ready["table_summary"]["reason_counts"].get("conflicting_field", 0) == int(generic_counterparty)
    root = f"/api/v1/cases/{case}"
    source_id = ready["import_id"]
    job = f"{root}/imports/{source_id}"
    confirmation = {"mode": ready["mode"], "mapping": ready["mapping"], "mark_accounts": [],
                    "ready_revision": ready["layout_review"]["revision"]}
    assert client.post(job + "/commit", json=confirmation).status_code == 200
    receipt = poll(client, case, source_id)["result"]
    expected_count = 2 if generic_counterparty else 3
    assert receipt["ok"] == expected_count
    with database._lock:
        database._con.close()
        database._con = None
    active = client.get(root + "/flows", params={"granularity": "observations"}).raise_for_status().json()["items"]
    assert len(active) == expected_count
    [first] = [flow for flow in active if flow["trade_no"] == "TX-A"]
    assert first["amount"] == "8.40" and first["currency"] == "CNY"
    assert (first["raw_payer_account"], first["raw_payee_account"]) == (payer, payee)
    side = "payee" if direction == "出" else "payer"
    assert first[side + "_name"] == "Synthetic party A"
    if generic_counterparty:
        review = client.get(job + "/review", params={"member_path": "table.csv", "view": "all"}).raise_for_status().json()
        [conflict] = [row for row in review["items"] if row["source_row"] == 4]
        assert conflict["disposition"] == "unresolved" and conflict["flow_id"] is None
        assert [cell["value"] for cell in conflict["original"]] == rows[-1]


@pytest.mark.parametrize("voucher", ["", "VOUCHER-02"])
def test_optional_tail_keeps_voucher_values_out_of_the_primary_reference(
        client, make_case, monkeypatch, voucher):
    from app import table_import
    from test_api_v1 import poll

    enable_trial(client, monkeypatch)
    case = make_case()
    _, rows = optional_metadata_source(tail=("交易发生地",))
    for row, value in zip(rows, ["凭证号", "", voucher, ""]):
        row.insert(6, value)
    for row, value in zip(rows, ["交易是否成功", "01", "01", "01"]):
        row.insert(7, value)
    content = io.StringIO(newline="")
    csv.writer(content, delimiter="\t").writerows(rows)
    ready = preview(client, case, content.getvalue().encode())
    if voucher:
        assert ready["table_summary"]["profile"] == table_import.CSV_TAIL_SOURCE_PROFILE
        assert ready["table_summary"]["accepted"] == 0
        return

    assert ready["table_summary"]["profile"] == table_import.CSV_TAIL_METADATA_PROFILE
    url = f"/api/v1/cases/{case}"
    job = f"{url}/imports/{ready['import_id']}"
    confirmation = {"mode": ready["mode"], "mapping": ready["mapping"], "mark_accounts": [],
                    "ready_revision": ready["layout_review"]["revision"]}
    assert client.post(job + "/commit", json=confirmation).status_code == 200
    assert poll(client, case, ready["import_id"])["result"]["ok"] == 3
    assert {row["trade_no"] for row in client.get(url + "/flows").raise_for_status().json()["items"]} == {
        "000-01", "000-02", "000-03"}
    journal = client.get(job + "/records", params={"member_path": "table.csv"}).raise_for_status().json()
    assert {item["details"]["reported_status"] for item in journal["items"] if item["source_row"] > 1} == {"unknown"}

@pytest.mark.parametrize("tail", [("MAC地址", "原交易键"), ("", ""), ("",) * 120])
def test_complete_rows_keep_financial_and_source_roles_when_final_tail_needs_review(
        client, database, make_case, monkeypatch, tail):
    from app import table_import
    from test_api_v1 import poll

    enable_trial(client, monkeypatch)
    case = make_case()
    raw, cells = optional_metadata_source(tail=tail)
    root = f"/api/v1/cases/{case}"
    ready = preview(client, case, raw)
    assert ready["table_summary"]["profile"] == table_import.CSV_TAIL_COMPLETE_ROWS_PROFILE
    assert ready["table_summary"]["reason_counts"] == {"transaction_valid": 2, "csv_tail_incomplete_record": 1}
    assert ready["automatic"] is False
    source_id = ready["import_id"]
    job = f"{root}/imports/{source_id}"
    body = {"mode": ready["mode"], "mapping": ready["mapping"], "mark_accounts": []}
    assert client.post(job + "/commit", json=body).status_code == 409
    body["ready_revision"] = ready["layout_review"]["revision"]
    assert client.post(job + "/commit", json=body).status_code == 200
    receipt = poll(client, case, source_id)["result"]
    assert receipt["ok"] == 2
    with database._lock:
        database._con.close()
        database._con = None
    rows = client.get(job + "/review", params={"member_path": "table.csv", "view": "all"}).raise_for_status().json()["items"]
    assert [[cell["value"] for cell in item["original"]] for item in rows] == cells[1:]
    assert [bool(item["flow_id"]) for item in rows] == [True, True, False]
    assert rows[-1]["reason_code"] == "csv_tail_incomplete_record"
    active = client.get(root + "/flows", params={"granularity": "observations"}).raise_for_status().json()
    assert {(row["trade_no"], row["amount"], row["raw_payer_account"], row["raw_payee_account"]) for row in active["items"]} == {
        ("000-01", "1.00", "0000021", "0000010"), ("000-02", "2.25", "0000010", "0000022")}
    assert active["funds"]["confirmed_event_count"] == 0
    pack = client.get(f"/api/cases/{case}/export").raise_for_status().content
    response = client.post("/api/cases/import", files={"file": ("truncated-record.fundcase", pack)})
    assert response.status_code == 200, response.text
    restored = response.json()
    assert client.get(f"/api/v1/cases/{restored['case_id']}/flows").raise_for_status().json()["total"] == 2
    source = f"{root}/sources/{source_id}"
    assert client.delete(source + "/operation", params={"expected_batch_id": receipt["batch_id"]}).status_code == 200
    assert client.get(root + "/flows").raise_for_status().json()["total"] == 0
    assert client.get(source + "/file").content == raw


@pytest.mark.parametrize("declared_amount_unit", [False, True])
def test_short_counterparty_suffix_keeps_complete_rows_and_separate_units(
        client, database, make_case, monkeypatch, declared_amount_unit):
    from app import case_packages, table_import
    from test_api_v1 import poll

    enable_trial(client, monkeypatch)
    case = make_case()
    amount_header = "交易金额(元)" if declared_amount_unit else "交易金额"
    header = ["交易流水号", "本方户名", "本方账号", "交易日期", "业务类别", "交易摘要", "借贷标志",
              amount_header, "余额(分)", "交易对方账号", "交易对方名称", "交易对方账号开户行"]
    values = [["SYN-1", "Synthetic Owner", "0012345", "2042-05-06", "synthetic", "", "借", "123", "123",
               "004321", "Synthetic Other", "Synthetic Bank"],
              ["SYN-2", "Synthetic Owner", "0012345", "2042-05-07", "synthetic", "", "贷", "5", "223", "", "", ""],
              ["SYN-3", "Synthetic Owner", "0012345", "2042-05-08", "synthetic", "", "借", "20", "201", "", "", ""],
              ["SYN-4", "Synthetic Owner", "0012345", "2042-05-09", "synthetic", "", "贷", "45", "295"]]
    stream = io.StringIO(newline="")
    csv.writer(stream, delimiter="\t").writerows([header, *values])
    raw = stream.getvalue().encode()
    with pytest.raises(table_import.TableImportError):
        table_import.parse(raw, suffix=".csv", profile=table_import.CSV_TAIL_COMPLETE_ROWS_PROFILE)
    for original, changed in (("交易对方账号开户行", "交易币种"), ("交易对方账号\t", "对方卡号\t"),
                              ("交易对方账号\t", "交易对手账卡号\t")):
        different_role = raw.replace(original.encode(), changed.encode(), 1)
        assert different_role != raw
        assert client.post(f"/api/v1/cases/{case}/imports/preview",
                           files={"file": ("synthetic.csv", different_role)}).status_code == 400
    book = preview(client, case, raw)
    summary = book["table_summary"]
    assert book["automatic"] is False
    assert summary["profile"] == table_import.CSV_TAIL_COUNTERPARTY_PROFILE
    assert summary["reason_counts"] == {"transaction_valid": 3, "csv_tail_incomplete_record": 1}
    money = {entry["field"]: entry for entry in summary["mapping_evidence"] if entry["field"] in {"amount", "balance"}}
    assert (money["amount"]["column_index"], money["amount"]["unit_factor"], money["amount"]["unit"]) == (
        "8", "1", "元" if declared_amount_unit else "元/默认基础单位")
    assert (money["balance"]["column_index"], money["balance"]["unit_factor"]) == ("9", "0.01")
    base = f"/api/v1/cases/{case}"
    job = base + f"/imports/{book['import_id']}"
    body = {"mode": book["mode"], "mapping": book["mapping"], "mark_accounts": [],
            "ready_revision": book["layout_review"]["revision"]}
    assert client.post(job + "/commit", json=body).status_code == 200
    receipt = poll(client, case, book["import_id"])["result"]
    assert receipt["ok"] == 3
    with database._lock:
        database._con.close()
        database._con = None
    result = client.get(job + "/review", params={"member_path": "table.csv", "view": "all"}).raise_for_status().json()["items"]
    assert [[cell["value"] for cell in row["original"]] for row in result] == values
    assert len(result[-1]["original"]) == 9 and not result[-1]["flow_id"]
    assert all(row["flow_id"] for row in result[:3])
    assert "历史默认币种" in result[0]["details"]["currency_basis"]
    flows = client.get(base + "/flows").raise_for_status().json()["items"]
    first = next(flow for flow in flows if flow["trade_no"] == "SYN-1")
    assert (first["amount"], first["balance"], first["currency"], first["raw_payee_account"]) == (
        "123.00", "1.23", "CNY", "004321")
    packed = case_packages.import_package(case_packages.export_package(case)[0])["case_id"]
    assert client.get(f"/api/v1/cases/{packed}/flows").raise_for_status().json()["total"] == 3
    source_path = base + f"/sources/{receipt['source_id']}"
    assert client.delete(source_path + "/operation", params={"expected_batch_id": receipt["batch_id"]}).status_code == 200
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0
    assert client.get(source_path + "/file").content == raw


def test_short_header_tail_with_split_amounts_retains_declared_balance_without_financial_admission(
        client, make_case, monkeypatch):
    from app import table_import

    enable_trial(client, monkeypatch)
    case = make_case()
    rows = [
        ("账号", "交易时间", "借方发生额", "贷方发生额", "帐户余额", "交易对手余额", "对方账号", ""),
        ("00021", "2040-02-09 11:12:13", "3.00", "0", "12.00", "50.00", "00032", ""),
        ("00021", "2040-02-09 11:12:14", "0", "2.001", "15.00", "40.00", "00037"),
    ]
    output = io.StringIO(newline="")
    csv.writer(output, delimiter="\t").writerows(rows)
    raw = output.getvalue().encode()
    ready = preview(client, case, raw)
    assert ready["table_summary"]["profile"] == table_import.CSV_TAIL_SOURCE_PROFILE
    assert ready["table_summary"]["accepted"] == 0
    root = f"/api/v1/cases/{case}"
    job = f"{root}/imports/{ready['import_id']}"
    def balance_notes():
        info = client.get(job + "/intake").raise_for_status().json()
        return [note for note in info["source_field_notes"] if "本方流水的原表余额" in note["text"]]
    [before] = balance_notes()
    assert before["columns"] == [1, 5] and before["rows"] == [2, 3]
    assert "笔后时点" in before["text"] and "交易对手余额" not in before["text"]
    result = complete(client, case, ready)
    assert result["result"]["ok"] == 0
    assert balance_notes() == [before]
    original = client.get(job + "/review", params={"member_path": "table.csv", "view": "all"}).raise_for_status().json()
    assert [[item["value"] for item in row["original"]] for row in original["items"]] == [list(row) for row in rows[1:]]
    assert client.get(root + "/flows").raise_for_status().json()["total"] == 0

def test_balance_roles_are_visible_for_header_only_positions_without_assigning_missing_values(
        client, make_case, monkeypatch):
    enable_trial(client, monkeypatch)
    case = make_case()
    rows = [("查询名称", "地区", "账号", "币种", "账户余额", "可用余额"),
            ("Synthetic A", "Synthetic place")]
    buffer = io.StringIO(newline="")
    csv.writer(buffer, delimiter="\t").writerows(rows)
    raw = buffer.getvalue().encode()
    ready = preview(client, case, raw)
    root = f"/api/v1/cases/{case}"
    job = f"{root}/imports/{ready['import_id']}"
    def description():
        response = client.get(job + "/intake").raise_for_status().json()
        [roles] = [note for note in response["source_field_notes"] if note["columns"] == [5, 6]]
        return roles
    before = description()
    assert before["rows"] == [] and "没有两列的原格" in before["text"]
    assert ready["table_summary"]["accepted"] == 0
    assert complete(client, case, ready)["result"]["ok"] == 0
    assert description() == before
    assert client.get(root + "/flows").raise_for_status().json()["total"] == 0


def test_truncated_remarks_keep_both_original_balance_columns_readable(client, make_case, monkeypatch):
    enable_trial(client, monkeypatch)
    case = make_case()
    head = ("账号", "交易时间", "收支标志", "交易金额", "交易余额", "交易对手余额", "币种", "备注")
    rows = [("000012", "2040-02-10 13:20:21", "进", "1.85", "13.85", "60.17", "CNY", ""),
            ("000012", "2040-02-10 13:20:22", "出", "0.15", "13.70", "", "CNY")]
    buffer = io.StringIO(newline="")
    csv.writer(buffer, delimiter="\t").writerows([head, *rows])
    view = preview(client, case, buffer.getvalue().encode())
    assert view["table_summary"]["profile"] == "funds-trace-table-v103"
    job = f"/api/v1/cases/{case}/imports/{view['import_id']}"
    def describe():
        card = client.get(job + "/intake").raise_for_status().json()
        [balance] = [note for note in card["source_field_notes"] if note["columns"] == [5, 6]]
        assert balance["rows"] == [2, 3] and "对方余额" in balance["text"]
        return balance
    note = describe()
    assert complete(client, case, view)["result"]["ok"] == 0
    assert note == describe()
    source = client.get(job + "/review", params={"member_path": "table.csv", "view": "all"}).raise_for_status().json()
    assert [[cell["value"] for cell in item["original"]] for item in source["items"]] == [list(row) for row in rows]
