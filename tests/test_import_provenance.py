"""Synthetic source records, atomic lineage, and portable case packages."""
import hashlib
import io
import json
from pathlib import Path
import uuid
import zipfile

import duckdb
import pandas as pd
import pytest

from app import case_packages, importer


HEADER = "交易时间,付款方账号,收款方账号,金额,摘要"


def read_rows(path):
    frame, sheet = importer.read_table(path)
    mode, mapping = importer.analyze(frame)
    rows, stats = importer.build_rows(frame, mode, mapping)
    return rows, stats, {
        "source_id": uuid.uuid4().hex, "filename": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size, "mode": mode, "mapping": mapping,
        "sheet": sheet, "parser_version": importer.PARSER_VERSION,
        "row_kind": frame.attrs["source_row_kind"],
    }


def csv_source(tmp_path, name="synthetic.csv"):
    path = tmp_path / name
    path.write_text(HEADER + "\n2026-01-01 10:00:00,001,002,123.45,synthetic\n", encoding="utf-8")
    return path


def commit_source(case_id, parsed):
    rows, stats, source = parsed
    return importer.commit_rows(source["filename"], source["mode"], rows,
                                stats=stats, case_id=case_id, provenance=source)


def package_members(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def archive_members(members):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return stream.getvalue()


def mutate_parquet(members, table, sql, tmp_path):
    source, target = tmp_path / "source.parquet", tmp_path / "target.parquet"
    source.write_bytes(members[f"{table}.parquet"])
    with duckdb.connect(config={"threads": 1}) as connection:
        connection.execute("create table rows as select * from read_parquet(?)", [str(source)])
        connection.execute(sql)
        connection.table("rows").write_parquet(str(target))
    members[f"{table}.parquet"] = target.read_bytes()


def test_csv_provenance_counts_logical_records_including_title_and_blank(tmp_path):
    path = tmp_path / "synthetic.csv"
    path.write_text("Synthetic title\n\n" + HEADER + "\n"
                    "2026-01-01 10:00:00,001,002,1.01,\"two\nphysical lines\"\n"
                    "\n2026-01-02 10:00:00,003,004,2.02,last\n", encoding="utf-8")
    rows, stats, source = read_rows(path)
    assert [row["_source_row"] for row in rows] == [4, 6]
    assert [row["_source_sheet"] for row in rows] == ["", ""]
    assert rows[0]["summary"] == "two\nphysical lines"
    assert source["sheet"] == ""
    assert stats["ok"] == 2


def test_excel_provenance_uses_real_worksheet_rows_after_empty_records(tmp_path):
    path = tmp_path / "synthetic.xlsx"
    frame = pd.DataFrame([
        ["Synthetic title", None, None, None, None],
        [None] * 5,
        HEADER.split(","),
        ["2026-01-01 10:00:00", "001", "002", "1.01", "first"],
        [None] * 5,
        ["2026-01-02 10:00:00", "003", "004", "2.02", "second"],
    ])
    frame.to_excel(path, index=False, header=False, sheet_name="Synthetic ledger")
    rows, _, source = read_rows(path)
    assert [row["_source_row"] for row in rows] == [4, 6]
    assert all(row["_source_sheet"] == "Synthetic ledger" for row in rows)
    assert source["sheet"] == "Synthetic ledger"


def test_bad_examples_include_trusted_source_records_and_do_not_invent_positions(tmp_path):
    path = tmp_path / "synthetic.csv"
    path.write_text("Synthetic title\n\n" + HEADER + "\n"
                    "not-a-date,001,002,1.01,invalid time\n"
                    "\n2026-01-02 10:00:00,,,2.02,missing accounts\n", encoding="utf-8")
    frame, _ = importer.read_table(path)
    mode, mapping = importer.analyze(frame)
    rows, stats = importer.build_rows(frame, mode, mapping)
    assert rows == []
    assert [example["source_row"] for example in stats["bad_examples"]] == [4, 6]
    assert [example["reason"] for example in stats["bad_examples"]] == ["交易时间无法解析", "缺少付款/收款账户"]
    frame.attrs.clear()
    _, historical_stats = importer.build_rows(frame, mode, mapping)
    assert all("source_row" not in example for example in historical_stats["bad_examples"])


def test_mode_a_self_name_mapping_survives_commit_and_package_round_trip(database, make_case, tmp_path):
    path = tmp_path / "synthetic.csv"
    path.write_text(HEADER + ",本方账号,本方户名\n"
                    "2026-01-01 10:00:00,001,002,1.01,synthetic,001,Synthetic owner\n", encoding="utf-8")
    frame, sheet = importer.read_table(path)
    mode, mapping = importer.analyze(frame)
    assert mode == "A"
    mapping.update(self_account="本方账号", self_name="本方户名")
    rows, stats = importer.build_rows(frame, mode, mapping)
    _, _, source = read_rows(path)
    source.update(mapping=mapping, sheet=sheet)
    case_id = make_case()
    assert commit_source(case_id, (rows, stats, source))["ok"] == 1
    assert database.q("select self_account,self_name from flows where case_id=?", [case_id]) == [
        {"self_account": "001", "self_name": "Synthetic owner"},
    ]
    imported = case_packages.import_package(case_packages.export_package(case_id)[0])["case_id"]
    imported_mapping = json.loads(database.q("select mapping_json from import_sources where case_id=?", [imported])[0]["mapping_json"])
    assert imported_mapping["self_name"] == "本方户名"
    assert imported_mapping["self_account"] == "本方账号"


def test_all_valid_duplicate_records_keep_exact_same_case_lineage(database, make_case, tmp_path):
    case_id, other_id = make_case("First"), make_case("Second")
    path = csv_source(tmp_path)
    first = read_rows(path)
    rows, stats, source = first
    rows.append({**rows[0], "_source_row": 3})
    stats.update(total=2, ok=2)
    result = commit_source(case_id, first)
    assert result["ok"] == 1 and result["rows_dup"] == 1
    repeat = read_rows(path)
    assert commit_source(case_id, repeat)["rows_dup"] == 1
    assert commit_source(other_id, read_rows(path))["ok"] == 1
    records = database.q("select case_id, source_row, flow_id, disposition from flow_sources order by case_id, disposition, source_row")
    first_records = [row for row in records if row["case_id"] == case_id]
    assert len(first_records) == 3
    assert len({row["flow_id"] for row in first_records}) == 1
    assert sorted(row["disposition"] for row in first_records) == ["duplicate", "duplicate", "imported"]
    assert records[-1]["case_id"] == other_id
    assert records[-1]["flow_id"] != first_records[0]["flow_id"]
    assert database.q("select count(*) n from flow_sources s join flows f on s.flow_id=f.id where s.case_id<>f.case_id") == [{"n": 0}]


def test_legacy_commit_does_not_invent_source(database, make_case, flow_row):
    importer.commit_rows("historical.csv", "A", [flow_row()], case_id=make_case())
    assert database.q("select count(*) n from import_sources") == [{"n": 0}]
    assert database.q("select count(*) n from flow_sources") == [{"n": 0}]


def test_lineage_write_failure_rolls_back_flows_batch_and_source(database, make_case, tmp_path, monkeypatch):
    case_id = make_case()
    original = database.insert_dataframe
    def fail_reference(table, frame, columns):
        if table == "flow_sources":
            assert database.q("select count(*) n from flows") == [{"n": 1}]
            assert database.q("select count(*) n from import_sources") == [{"n": 1}]
            raise RuntimeError("synthetic source write failure")
        original(table, frame, columns)
    monkeypatch.setattr(database, "insert_dataframe", fail_reference)
    with pytest.raises(RuntimeError, match="synthetic source write failure"):
        commit_source(case_id, read_rows(csv_source(tmp_path)))
    for table in ("flows", "batches", "flow_sources", "import_sources"):
        assert database.q(f"select count(*) n from {table}") == [{"n": 0}]


def test_later_flow_chunk_failure_rolls_back_source_and_all_rows(database, make_case, tmp_path, monkeypatch):
    case_id = make_case()
    original = database.insert_dataframe
    chunk_sizes = []
    def fail_second_flow_chunk(table, frame, columns):
        if table == "flows":
            chunk_sizes.append(len(frame))
            if len(chunk_sizes) == 2:
                raise RuntimeError("synthetic second flow chunk failure")
        original(table, frame, columns)
    monkeypatch.setattr(database, "insert_dataframe", fail_second_flow_chunk)
    rows, stats, source = read_rows(csv_source(tmp_path))
    rows = [{**rows[0], "trade_no": f"SYN-{index}", "_source_row": index + 2}
            for index in range(50001)]
    stats.update(total=len(rows), ok=len(rows))
    with pytest.raises(RuntimeError, match="synthetic second flow chunk failure"):
        commit_source(case_id, (rows, stats, source))
    assert chunk_sizes == [50000, 1]
    for table in ("flows", "batches", "flow_sources", "import_sources"):
        assert database.q(f"select count(*) n from {table}") == [{"n": 0}]


def test_source_of_legacy_duplicate_rows_points_stably_to_one_existing_id(database, make_case, tmp_path):
    case_id = make_case()
    rows, stats, source = read_rows(csv_source(tmp_path))
    row = {key: value for key, value in rows[0].items() if not key.startswith("_")}
    row["currency"] = row["currency"] or "CNY"
    cols = list(row)
    old_ids = []
    for _ in range(2):
        old_ids.append(database.q(f"insert into flows(case_id,{','.join(cols)}) values (?,{','.join('?' for _ in cols)}) returning id",
                                 [case_id, *row.values()])[0]["id"])
    assert commit_source(case_id, (rows, stats, source))["ok"] == 0
    assert database.q("select flow_id,disposition from flow_sources where case_id=?", [case_id]) == [
        {"flow_id": min(old_ids), "disposition": "duplicate"},
    ]
    assert [record["id"] for record in database.q("select id from flows where case_id=? order by id", [case_id])] == sorted(old_ids)


@pytest.mark.parametrize("bad", ["hash", "row", "sheet", "mapping"])
def test_invalid_source_metadata_does_not_write_partial_data(database, make_case, tmp_path, bad):
    rows, stats, source = read_rows(csv_source(tmp_path))
    if bad == "hash":
        source["sha256"] = "not a hash"
    elif bad == "row":
        rows[0]["_source_row"] = 0
    elif bad == "sheet":
        rows[0]["_source_sheet"] = "Wrong sheet"
    else:
        source["mapping"] = {"amount": ["金额"]}
    with pytest.raises(importer.ImportError):
        commit_source(make_case(), (rows, stats, source))
    assert database.q("select count(*) n from batches") == [{"n": 0}]


def test_package_round_trip_remaps_sources_and_flows_without_raw_upload(database, make_case, tmp_path):
    original = make_case()
    parsed = read_rows(csv_source(tmp_path))
    commit_source(original, parsed)
    before = database.q("select source_id,flow_id,source_row from flow_sources where case_id=?", [original])[0]
    content, _ = case_packages.export_package(original)
    members = package_members(content)
    assert json.loads(members["meta.json"])["format"] == 2
    assert not any(name.endswith(".csv") for name in members)
    result = case_packages.import_package(content)
    imported = result["case_id"]
    after = database.q("select source_id,flow_id,source_row from flow_sources where case_id=?", [imported])[0]
    assert after["source_id"] != before["source_id"]
    assert after["flow_id"] != before["flow_id"]
    assert after["source_row"] == before["source_row"] == 2
    source = database.q("select filename,sha256,origin from import_sources where case_id=?", [imported])[0]
    assert source == {"filename": "synthetic.csv", "sha256": parsed[2]["sha256"], "origin": "package"}
    assert database.q("select count(*) n from import_sessions where case_id=?", [imported]) == [{"n": 0}]


def test_format_one_package_without_lineage_remains_readable(database, make_case, tmp_path):
    source_case = make_case()
    commit_source(source_case, read_rows(csv_source(tmp_path)))
    content, _ = case_packages.export_package(source_case)
    members = package_members(content)
    meta = json.loads(members["meta.json"])
    meta["format"] = 1
    meta.pop("source_files")
    members["meta.json"] = json.dumps(meta).encode()
    for table in case_packages.PROVENANCE_PKG_COLS:
        members.pop(f"{table}.parquet")
    imported = case_packages.import_package(archive_members(members))["case_id"]
    assert database.q("select count(*) n from flows where case_id=?", [imported]) == [{"n": 1}]
    assert database.q("select count(*) n from import_sources where case_id=?", [imported]) == [{"n": 0}]


@pytest.mark.parametrize("mutation", ["hash", "flow", "source", "row", "fractional_row", "sheet", "row_kind", "mapping", "disposition", "column", "one_table"])
def test_invalid_package_lineage_never_creates_partial_case(database, make_case, tmp_path, mutation):
    case_id = make_case()
    commit_source(case_id, read_rows(csv_source(tmp_path)))
    members = package_members(case_packages.export_package(case_id)[0])
    if mutation == "one_table":
        members.pop("flow_sources.parquet")
    else:
        table, sql = {
            "hash": ("import_sources", "update rows set sha256='bad'"),
            "flow": ("flow_sources", "update rows set flow_id=999999"),
            "source": ("flow_sources", "update rows set source_id='aabbccddeeff'"),
            "row": ("flow_sources", "update rows set source_row=0"),
            "fractional_row": ("flow_sources", "alter table rows alter column source_row type double; update rows set source_row=2.5"),
            "sheet": ("import_sources", "update rows set sheet='invented sheet'"),
            "row_kind": ("import_sources", "update rows set row_kind='worksheet_row'"),
            "mapping": ("import_sources", "update rows set mapping_json='{\"invented\":\"金额\"}'"),
            "disposition": ("flow_sources", "update rows set disposition='invented'"),
            "column": ("import_sources", "alter table rows add column extra varchar"),
        }[mutation]
        mutate_parquet(members, table, sql, tmp_path)
    before = database.q("select count(*) n from cases")
    with pytest.raises(Exception) as raised:
        case_packages.import_package(archive_members(members))
    assert raised.value.status_code == 400
    assert database.q("select count(*) n from cases") == before


def test_case_delete_queues_only_its_managed_uploads_and_is_atomic(database, make_case, tmp_path, monkeypatch):
    case_id, other = make_case("Delete"), make_case("Keep")
    parsed = read_rows(csv_source(tmp_path))
    commit_source(case_id, parsed)
    for cid, import_id in [(case_id, parsed[2]["source_id"]), (other, uuid.uuid4().hex)]:
        database.x("insert into import_sessions(import_id,case_id,surface,filename,suffix,sha256,size_bytes) values (?,?,'v1','synthetic.csv','.csv',?,1)", [import_id, cid, parsed[2]["sha256"]])
    from contextlib import contextmanager
    original = database.transaction
    @contextmanager
    def fail_commit():
        with original() as connection:
            yield connection
            raise RuntimeError("synthetic delete commit failure")
    monkeypatch.setattr(database, "transaction", fail_commit)
    with pytest.raises(RuntimeError, match="synthetic delete commit failure"):
        case_packages.delete_package_case(case_id)
    assert database.q("select count(*) n from import_file_gc") == [{"n": 0}]
    assert database.q("select count(*) n from flow_sources where case_id=?", [case_id]) == [{"n": 1}]
    monkeypatch.setattr(database, "transaction", original)
    case_packages.delete_package_case(case_id)
    assert database.q("select import_id from import_file_gc") == [{"import_id": parsed[2]["source_id"]}]
    for table in ("flows", "batches", "import_sources", "flow_sources", "import_sessions"):
        assert database.q(f"select count(*) n from {table} where case_id=?", [case_id]) == [{"n": 0}]
    assert database.q("select count(*) n from import_sessions where case_id=?", [other]) == [{"n": 1}]
