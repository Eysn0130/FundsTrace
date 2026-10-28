"""Final-envelope binding equals the prior member-then-envelope path."""
import copy
import datetime as dt
import io
import itertools
import uuid

import pytest

from app import evidence_resolution, import_staging, table_archive, table_import
from app.services import imports
from test_folder_evidence import account, archive, complete, preview, table, transaction
from test_table_cancellations import round_trip, source


def members(kind):
    if kind == "cancellation":
        return [("rows.csv", source(round_trip())[0])]
    if kind == "duplicate-bytes":
        content = source(round_trip())[0]
        return [("first.csv", content), ("second.csv", content)]
    if kind == "auxiliary-only":
        return [("account.csv", table("account", [account()])),
                ("subaccount.csv", table("subaccount", [{"银行名称": "Synthetic bank", "开户账号": "SYN-A", "子账户账号": "SYN-S"}]))]
    if kind == "omitted-bank":
        return [("transactions.csv", table("transaction", [transaction()])),
                ("account.csv", table("account", [account(bank="")])),
                ("subaccount.csv", table("subaccount", [{"开户账号": "SYN-A", "子账户账号": "SYN-S"}]))]
    if kind == "parent-child":
        return [("SYN-REQUEST/Synthetic bank交易明细信息.csv", table("transaction", [transaction(**{"交易账号": "SYN-S"})])),
                ("account.csv", table("account", [account(number="SYN-P")])),
                ("subaccount.csv", table("subaccount", [{"银行名称": "Synthetic bank", "开户账号": "SYN-P", "子账户账号": "SYN-S", "币种": "CNY"}])),
                ("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank"}]))]
    if kind == "nested":
        return [("first.zip", archive(members("parent-child"))),
                ("second.zip", archive(members("parent-child"))),
                ("other.zip", archive(members("omitted-bank"))),
                *members("cancellation")]
    raise AssertionError(kind)


def eager_parser(original):
    def parse(*args, **kwargs):
        # Restore exactly the pre-optimization member binding; the complete
        # envelope still executes its unchanged final binding afterwards.
        kwargs["_defer_occurrence_binding"] = False
        return original(*args, **kwargs)
    return parse


def frozen_plan(content, profile):
    plan = table_archive.parse(io.BytesIO(content), profile=profile)
    try:
        # PackedDetails.__deepcopy__ produces independent plain dictionaries.
        # Only the live resource owner is excluded, never a business field.
        return copy.deepcopy({key: value for key, value in plan.items() if key != "_details_pool"})
    finally:
        plan["_details_pool"].close()


@pytest.mark.parametrize("profile", sorted(table_archive.EVIDENCE_PROFILES))
@pytest.mark.parametrize("kind", ["cancellation", "duplicate-bytes", "auxiliary-only", "omitted-bank", "parent-child", "nested"])
def test_one_envelope_binding_preserves_every_plan_value(monkeypatch, profile, kind):
    content = archive(members(kind))
    bind, calls = evidence_resolution.bind_occurrences, []

    def observe(plan):
        calls.append(tuple(member["member_path"] for member in plan["members"]))
        return bind(plan)

    monkeypatch.setattr(evidence_resolution, "bind_occurrences", observe)
    actual = frozen_plan(content, profile)
    assert len(calls) == 1
    assert calls[0] == tuple(member["member_path"] for member in actual["members"])
    calls.clear()
    with monkeypatch.context() as old:
        old.setattr(table_import, "parse", eager_parser(table_import.parse))
        expected = frozen_plan(content, profile)
    assert len(calls) == (1 if kind == "auxiliary-only" else 1 + sum(member["role"] == "table" for member in expected["members"]))
    assert actual == expected
    assert all(type(record["details"]) is dict for record in actual["records"])
    if kind in {"cancellation", "duplicate-bytes", "nested"}:
        assert actual["stats"]["cancelled"] == (4 if kind == "duplicate-bytes" else 2)
    if kind == "parent-child":
        assert actual["rows"][0]["_occurrence_scope"] == "Synthetic bank"
    if kind == "omitted-bank":
        assert actual["rows"][0]["_occurrence_scope"] == ""


def test_standalone_parser_still_binds_and_cannot_defer_without_envelope():
    content = source(round_trip())[0]
    plan = table_import.parse(content, suffix=".csv", profile=table_import.EVIDENCE_PROFILE)
    assert all(row["_occurrence_key"] for row in plan["rows"])
    assert all(record["details"]["evidence_family"] for record in plan["records"])
    with pytest.raises(table_import.TableImportError, match="延后来源绑定"):
        table_import.parse(content, suffix=".csv", profile=table_import.EVIDENCE_PROFILE,
                           _defer_occurrence_binding=True)


@pytest.mark.parametrize("eager", [False, True])
def test_duplicate_member_paths_remain_rejected(monkeypatch, eager):
    if eager:
        monkeypatch.setattr(table_import, "parse", eager_parser(table_import.parse))
    with pytest.warns(UserWarning, match="Duplicate name"):
        content = archive([*members("cancellation"), *members("cancellation")])
    with pytest.raises(table_import.TableImportError, match="重复成员路径"):
        table_archive.parse(io.BytesIO(content), profile=table_archive.EVIDENCE_PROFILE)


def test_final_binding_failure_closes_spill_and_retains_archive_error(database, monkeypatch):
    monkeypatch.setattr(import_staging, "MEMORY_PAYLOAD_BYTES", 0)
    initialize, pools = import_staging.DetailsPool.__init__, []

    def capture(self):
        initialize(self)
        pools.append(self)

    def fail(_plan):
        raise ValueError("Synthetic final binding failure")

    monkeypatch.setattr(import_staging.DetailsPool, "__init__", capture)
    monkeypatch.setattr(evidence_resolution, "bind_occurrences", fail)
    with pytest.raises(table_import.TableImportError) as caught:
        table_archive.parse(io.BytesIO(archive(members("cancellation"))), profile=table_archive.EVIDENCE_PROFILE)
    assert caught.value.__traceback__ is not None
    assert pools and all(pool._stream.closed for pool in pools)


def test_public_commit_and_replay_match_eager_binding(client, database, make_case, monkeypatch, tmp_path):
    original_parse = table_import.parse
    selected_members = members("nested")
    results, revisions = [], []
    for index, parser in enumerate((eager_parser(original_parse), original_parse)):
        # Identical synthetic databases make IDs comparable; no live instance
        # or source file participates in this public API equivalence test.
        with database._lock:
            database._con.close()
            database._con = None
            directory = tmp_path / f"binding-{index}"
            directory.mkdir()
            monkeypatch.setattr(database, "DATA_DIR", directory)
            monkeypatch.setattr(database, "DB_PATH", directory / "fund.duckdb")
            database.con()
        ids = itertools.count(1)
        monkeypatch.setattr(imports.uuid, "uuid4", lambda: uuid.UUID(int=next(ids)))
        monkeypatch.setattr(imports, "_now", lambda: dt.datetime(2026, 10, 25, 12))
        monkeypatch.setattr(table_import, "parse", parser)
        cid = make_case("Synthetic envelope binding equivalence")
        receipts, reports, previews = [], [], []
        for _ in range(2):
            view = preview(client, cid, selected_members, scope_bank=None)
            job = complete(client, cid, view)
            assert job["stage"] == "done", job
            assert job["result"]["table_summary"]["cancelled"] == 2
            report = client.get(f"/api/v1/cases/{cid}/sources/{job['result']['source_id']}/report")
            assert report.status_code == 200, report.text
            previews.append(view)
            receipts.append(job["result"])
            reports.append(report.json())
        assert receipts[1]["ok"] == 0
        stats = client.get(f"/api/v1/cases/{cid}/stats").json()
        revision = stats.pop("analysis_revision")
        assert isinstance(revision, str) and len(revision) == 32
        revisions.append(revision)
        facts = database.q_raw("select * from flows where case_id=? order by id", [cid])
        for row in facts:
            if "created_at" in row:
                row["created_at"] = "operational timestamp"
        results.append({"previews": previews, "receipts": receipts, "reports": reports,
                        "facts": facts, "stats": stats})
    assert revisions[0] != revisions[1]
    assert results[0] == results[1]
