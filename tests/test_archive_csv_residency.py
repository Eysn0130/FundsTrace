"""Synthetic CSV parsing releases consumed members before decoding the next."""
import copy
import csv
from decimal import Decimal
import io
import weakref

import pytest

from app import import_staging as staging, table_archive, table_import
from app.services import imports
from test_folder_evidence import archive, complete


HEAD = ["date", "payer_account", "payee_account", "amount", "currency", "balance", "description"]
ROWS = [
    ["2026-01-01 10:00:00.123456", "000SYN-A", "000SYN-B", "90071992547409.91", "CNY", "", "SYN raw, text;with|delimiters\tinside"],
    ["2026-01-02 10:00:00.654321", "000SYN-C", "000SYN-D", "0.03", "USD", "0.00", "SYN separate currency"],
]
ORDINARY_PROFILES = [table_archive.SOURCE_OBSERVATION_PROFILE, table_archive.PROFILE]
ALL_PROFILES = sorted(table_import.SUPPORTED_PROFILES | table_archive.SUPPORTED_PROFILES)


def csv_bytes(rows):
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def close_plan(plan):
    for key in ("_plan_store", "_details_pool"):
        if plan.get(key) is not None:
            plan[key].close()


def observe_stores(monkeypatch):
    initialize, decode = staging.PlanStore.__init__, table_import._csv
    stores, prior_records = [], []

    def create(self):
        initialize(self)
        stores.append(self)

    def decoded(*args, **options):
        active = [store for store in stores if not store._closed]
        prior_records.append(len(active[-1].sequence("records")) if active else None)
        return decode(*args, **options)

    monkeypatch.setattr(staging.PlanStore, "__init__", create)
    monkeypatch.setattr(table_import, "_csv", decoded)
    return stores, prior_records


def test_public_ordinary_preview_stages_each_member_before_the_next(client, database, make_case, monkeypatch):
    stores, prior_records = observe_stores(monkeypatch)
    payload = archive([("first.csv", csv_bytes([HEAD, *ROWS])),
                       ("second.csv", csv_bytes([HEAD, ROWS[1]]))])
    cid = make_case("Synthetic member residency")
    try:
        response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("synthetic.zip", payload)})
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["table_summary"]["profile"] == table_archive.PROFILE
        assert prior_records == [0, 2]
        job = complete(client, cid, view)
        assert job["stage"] == "done" and job["result"]["ok"] == 3, job
        facts = database.q_raw("select payer_account,payee_account,amount,currency,balance,trade_time,summary from flows where case_id=? order by id", [cid])
        assert [(row["payer_account"], row["payee_account"], row["amount"], row["currency"], row["balance"],
                 row["trade_time"].microsecond, row["summary"]) for row in facts] == [
            ("000SYN-A", "000SYN-B", Decimal("90071992547409.91"), "CNY", None, 123456, ROWS[0][-1]),
            ("000SYN-C", "000SYN-D", Decimal("0.03"), "USD", Decimal("0.00"), 654321, ROWS[1][-1]),
            ("000SYN-C", "000SYN-D", Decimal("0.03"), "USD", Decimal("0.00"), 654321, ROWS[1][-1]),
        ]
        assert database.q_raw("select member_path,source_row from import_records where case_id=? order by record_no", [cid]) == [
            {"member_path": "first.csv", "source_row": 2}, {"member_path": "first.csv", "source_row": 3},
            {"member_path": "second.csv", "source_row": 2}]
        assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == payload
    finally:
        imports.import_preview_cache.discard()
    assert stores and all(store._closed for store in stores)


@pytest.mark.parametrize("profile", ORDINARY_PROFILES)
def test_staged_ordinary_members_preserve_every_plan_field(database, profile):
    payload = archive([("first.csv", csv_bytes([HEAD, *ROWS])),
                       ("nested.zip", archive([("second.csv", csv_bytes([HEAD, ROWS[1]]))]))])
    expected = table_archive.parse(io.BytesIO(payload), profile=profile)
    actual = table_archive.parse(io.BytesIO(payload), profile=profile, _stage=True)
    try:
        assert "_plan_store" in actual
        assert set(actual) - {"_plan_store"} == set(expected)
        for key in expected:
            value = list(actual[key]) if isinstance(actual[key], staging.StagedSequence) else actual[key]
            assert copy.deepcopy(value) == copy.deepcopy(expected[key]), key
    finally:
        close_plan(actual)
        close_plan(expected)


@pytest.mark.parametrize("profile", ORDINARY_PROFILES)
def test_ordinary_member_releases_consumed_csv_cells(monkeypatch, profile):
    decode, interpret = table_import._csv, table_import._interpret_candidates
    matrices, consumed = [], []

    def decoded(*args, **options):
        sheets = decode(*args, **options)
        matrices.append(sheets[0]["rows"])
        return sheets

    def interpreted(*args, **options):
        consumed.append(all(row is None for row in matrices[-1][:len(consumed) + 1]))
        return interpret(*args, **options)

    monkeypatch.setattr(table_import, "_csv", decoded)
    monkeypatch.setattr(table_import, "_interpret_candidates", interpreted)
    plan = table_archive.parse(io.BytesIO(archive([("source.csv", csv_bytes([HEAD, *ROWS]))])), profile=profile)
    try:
        assert consumed == [True, True]
        assert len(plan["rows"]) == 2 and plan["rows"][0]["amount"] == Decimal(ROWS[0][3])
    finally:
        close_plan(plan)


def test_strict_csv_keeps_one_complete_choice_between_delimiters(monkeypatch):
    collect = table_import._collect_rows
    references, retained = [], []

    class Matrix(list):
        pass

    def collected(*args, **options):
        retained.append(sum(reference() is not None for reference in references))
        rows = Matrix(collect(*args, **options))
        references.append(weakref.ref(rows))
        return rows

    monkeypatch.setattr(table_import, "_collect_rows", collected)
    text = "SYN-a,SYN-b;SYN-c|SYN-d\tSYN-e\nSYN-f,SYN-g;SYN-h|SYN-i\tSYN-j\n"
    with pytest.raises(table_import.TableImportError, match="CSV分隔符存在歧义"):
        table_import._strict_source_csv(text, table_import.PROFILE)
    assert len(retained) == 4
    assert max(retained) <= 1


def test_late_csv_damage_closes_staging_and_rejects_the_entire_public_batch(client, database, make_case, monkeypatch):
    stores, prior_records = observe_stores(monkeypatch)
    payload = archive([("first.csv", csv_bytes([HEAD, *ROWS])), ("damaged.csv", b'date,amount\n"unterminated')])
    cid = make_case("Synthetic late CSV damage")
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("synthetic.zip", payload)})
    assert response.status_code == 400 and "CSV引号或记录结构损坏" in response.text
    assert prior_records == [0, 2]
    assert stores and all(store._closed for store in stores)
    for name in ("flows", "batches", "import_sources", "import_members", "import_records", "import_sessions"):
        assert database.q_raw(f"select count(*) n from {name} where case_id=?", [cid]) == [{"n": 0}]


@pytest.mark.parametrize("profile", [table_import.IDENTITY_PROFILE, table_import.SOURCE_CONTRACT_PROFILE,
                                     table_archive.PROFILE, table_archive.AUXILIARY_ROWS_EVIDENCE_PROFILE])
def test_header_search_skips_impossible_rows_and_retains_prefix_false_positives(monkeypatch, profile):
    candidates, calls = table_import._candidates, []
    late = ["date", "payer_account", "payee_account", "amount"]
    possible = ["date_not_a_header", "SYN text"]
    rows = [["SYN ordinary value", "123.45"] for _ in range(100)] + [possible, late]

    def observed(row, selected):
        calls.append(row)
        return candidates(row, selected)

    monkeypatch.setattr(table_import, "_candidates", observed)
    index, found, mode = table_import._locate_header(rows, profile)
    assert index == 101 and mode == "A" and found.get("trade_time") and found.get("amount")
    assert calls == [possible, late]


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_alias_free_builtin_rows_keep_each_current_profile_result(profile):
    rows = [[], ["SYN ordinary text"], ("2036-01-01", "17.01"),
            ["SYN code", "000SYN-account", "currency"]]
    for row in rows:
        found = table_import._candidates(row, profile)
        assert not found.get("trade_time")
    assert table_import._locate_header(rows, profile) == (None, {}, None)


def test_header_search_keeps_custom_profiles_rows_and_cells_on_the_original_path(monkeypatch):
    candidates, calls = table_import._candidates, []

    class Profile(str):
        pass

    class Cell(str):
        pass

    class Row(list):
        def __len__(self):
            raise AssertionError("custom row length must not be inspected")

    def observed(row, selected):
        calls.append(row)
        return candidates(row, selected)

    monkeypatch.setattr(table_import, "_candidates", observed)
    cases = [(Profile(table_import.PROFILE), ["SYN ordinary text", "123"]),
             (table_import.PROFILE, Row(["SYN ordinary text"])),
             (table_import.PROFILE, [Cell("SYN ordinary text")]),
             (table_import.PROFILE, [None]), (table_import.PROFILE, [0]),
             (table_import.PROFILE, [table_import._WorkbookValue("SYN ordinary text", formula=True)])]
    for profile, row in cases:
        calls.clear()
        assert table_import._locate_header([row], profile) == (None, {}, None)
        assert len(calls) == 1 and calls[0] is row


@pytest.mark.parametrize("profile", [table_import.IDENTITY_PROFILE, table_import.FINANCIAL_PROFILE,
                                     table_import.TIME_FORMAT_PROFILE, table_import.PRECISE_PROFILE,
                                     table_import.SOURCE_CONTRACT_PROFILE, table_import.PROFILE,
                                     table_archive.PROFILE, table_archive.AUXILIARY_ROWS_EVIDENCE_PROFILE])
def test_time_name_hint_preserves_the_complete_current_vocabulary(profile):
    words = set().union(*(vocabulary.get("trade_time", set()) for vocabulary in (
        table_import._ALIASES, table_import._FINANCIAL_ALIASES, table_import._CONTRACT_ALIASES)))
    words |= table_import._CLOCK_HEADERS
    variants = set(words)
    variants |= {" _\t".join(word) for word in words}
    variants |= {" _" * 80 + word + " _" * 80 for word in words}
    variants |= {"".join(chr(ord(char) + 0xFEE0) if 0x21 <= ord(char) <= 0x7E else char for char in word) for word in words}
    variants |= {word + suffix for word in words for suffix in (
        "(元)", "[单位:元]", "(YYYY-MM-DD HH:MM:SS)", "[HHMMSS]", "(SYN unsupported)")}
    for word in variants:
        row = [word, "payer_account", "payee_account", "amount"]
        found = table_import._candidates(row, profile)
        expected = ((0, found, table_import._shape(found, profile))
                    if found.get("trade_time") and any(found.get(field) for field in ("amount", "income_amt", "expense_amt"))
                    else (None, {}, None))
        assert table_import._locate_header([row], profile) == expected, (profile, word)


@pytest.mark.parametrize("name", ["交易时间(元)", "交易时间[单位:元]", " _" * 80 + "交易发生时间" + " _" * 80])
def test_late_time_headers_keep_invalid_declarations_and_original_positions(name):
    raw = csv_bytes([["SYN preamble", "SYN text"],
                     [name, "payer_account", "payee_account", "amount"],
                     ["2026-01-01 10:00:00.123456", "000SYN-A", "000SYN-B", "1.03"]])
    plan = table_archive.parse(io.BytesIO(archive([("source.csv", raw)])))
    try:
        assert plan["documents"][0]["header_row"] == 2
        assert [record["source_row"] for record in plan["records"]] == [1, 3]
        if "元" in name:
            assert plan["rows"] == []
            assert plan["records"][-1]["reason_code"] == "invalid_time"
        else:
            assert len(plan["rows"]) == 1 and plan["rows"][0]["amount"] == Decimal("1.03")
            assert plan["rows"][0]["trade_time"].microsecond == 123456
    finally:
        close_plan(plan)


def test_header_search_reads_current_aliases_without_a_stale_cache(monkeypatch):
    alias = table_import._norm("SYN newly declared clock")
    monkeypatch.setitem(table_import._ALIASES, "trade_time", table_import._ALIASES["trade_time"] | {alias})
    row = ["SYN newly declared clock", "payer_account", "payee_account", "amount"]
    assert table_import._locate_header([row], table_import.PROFILE)[0] == 0
