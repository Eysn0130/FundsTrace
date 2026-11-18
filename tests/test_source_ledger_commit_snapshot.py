"""Source publication reads one private snapshot of the uncommitted journal."""
from decimal import Decimal
import json
import os
import stat
import weakref

import pytest

from app import import_staging, import_validation, source_ledger, table_archive, table_import


SOURCE = "synthetic-ledger-snapshot"
MEMBER = "ledger.csv"
MEMBER_SHA = "a" * 64


def evidence(position, *, wide=False, kind="ledger"):
    amount = "-0.01" if kind == "ledger" else "0.01"
    row = {"trade_no": f"SYN-{position}", "trade_time": "2038-02-04 09:00:00.123456",
           "payer_name": "Synthetic owner", "payer_account": "000SYN-A", "payer_bank": "Synthetic bank",
           "payee_name": "Synthetic counterparty", "payee_account": "000SYN-B", "payee_bank": "",
           "amount": amount, "balance": "100.00", "currency": "CNY",
           "summary": ("Synthetic wide source note " * 2048) if wide else "Synthetic source note",
           "channel": "", "self_account": "000SYN-A", "self_name": "Synthetic owner",
           "trade_time_utc": None, "trade_time_original": None, "trade_time_offset": None}
    return {"policy": source_ledger.POLICY, "kind": kind, "header_scope": 1,
            "row": row, "signed_amount": amount, "source_currency": "CNY", "source_direction": "出",
            "direction": "out", "own_card": "", "reported_status": "source_reported_success",
            "issues": ["negative_amount"] if kind == "ledger" else [], "balance_before": None}


def seed(database, cid, positions, *, wide=False, damage=None):
    batch = database.q_raw("insert into batches(case_id,filename,mode) values (?,'synthetic.zip','B') returning id", [cid])[0]["id"]
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,mapping_json,sheet,parser_version,row_kind)
        values (?,?,'synthetic.zip',?,0,'B','{}','',?,'table_record')""", [SOURCE, cid, "b" * 64, table_archive.AUXILIARY_ROWS_EVIDENCE_PROFILE])
    database.x("insert into import_members values (?,?,?,'table',?,0,'utf-8-sig',?)",
               [cid, SOURCE, MEMBER, MEMBER_SHA, len(positions)])
    for number, position in enumerate(positions, 1):
        value = evidence(position, wide=wide)
        if number == len(positions) and damage is not None:
            damage(value)
        details = json.dumps({source_ledger.FIELD: json.dumps(value, ensure_ascii=False)}, ensure_ascii=False)
        database.x("insert into import_records values (?,?,?,?,?,?,?,?,?)", [
            cid, SOURCE, number, MEMBER, position, table_import.record_key(MEMBER, MEMBER_SHA, position),
            "unresolved", "negative_amount", details])
    return batch


class LedgerText(str):
    pass


def observe_snapshot_query(database, monkeypatch):
    connection = database.con()
    state = {"connection": connection, "executions": 0, "sizes": [], "lengths": [], "drained": False, "references": [], "peak_live": 0}

    class Cursor:
        @property
        def description(self):
            return connection.description

        def fetchall(self):
            raise AssertionError("source ledger snapshot must never fetchall")

        def fetchmany(self, size):
            state["sizes"].append(size)
            rows = connection.fetchmany(size)
            result = []
            for row in rows:
                text = LedgerText(row[-1])
                state["references"].append(weakref.ref(text))
                result.append((*row[:-1], text))
            state["lengths"].append(len(result))
            state["peak_live"] = max(state["peak_live"], sum(ref() is not None for ref in state["references"]))
            if not result:
                state["drained"] = True
            return result

    class Connection:
        def execute(self, sql, params=None):
            normalized = " ".join(sql.split()).lower()
            result = connection.execute(sql, params or [])
            if normalized.startswith("select record_no,member_path,member_sha256,source_row,disposition,ledger_evidence from source_ledger_records"):
                assert normalized.endswith("where case_id=? and source_id=? order by record_no")
                state["executions"] += 1
                return Cursor()
            return result

        def __getattr__(self, name):
            return getattr(connection, name)

    monkeypatch.setattr(database, "con", lambda: Connection())
    return state


@pytest.mark.parametrize("chunk", [1, 2, 4])
def test_snapshot_drains_one_bounded_query_before_writes_and_releases_raw_rows(database, make_case, monkeypatch, chunk):
    cid = make_case("Synthetic bounded ledger snapshot")
    monkeypatch.setattr(source_ledger, "_COMMIT_CHUNK", chunk)
    with database.import_transaction():
        batch = seed(database, cid, list(range(2, 19)), wide=True)
        state = observe_snapshot_query(database, monkeypatch)
        publish = source_ledger._commit_records
        publications = []

        def checked(*args):
            assert state["drained"], "shared DuckDB result must be exhausted before publication"
            assert not any(ref() is not None for ref in state["references"]), "snapshot must release all raw ledger rows before publication"
            publications.append(len(args[-1]))
            # DuckDB's local pandas replacement scan requires the real execute
            # frame. Its bounded publication queries use this same connection.
            with monkeypatch.context() as writes:
                writes.setattr(database, "con", lambda: state["connection"])
                return publish(*args)

        monkeypatch.setattr(source_ledger, "_commit_records", checked)
        assert source_ledger.commit(cid, SOURCE, batch) == {"added": 17, "duplicates": 0}
        assert state["executions"] == 1
        assert state["sizes"] and set(state["sizes"]) == {chunk}
        assert max(state["lengths"]) <= chunk and state["lengths"][-1] == 0
        assert state["peak_live"] <= 2 * chunk
        assert max(publications) <= chunk and sum(publications) == 17
    rows = database.q_raw("select trade_no,amount,trade_time,summary from flows where case_id=? order by id", [cid])
    assert [row["trade_no"] for row in rows] == [f"SYN-{n}" for n in range(2, 19)]
    assert all(row["amount"] == Decimal("-0.01") and row["trade_time"].microsecond == 123456 for row in rows)
    assert all(row["summary"] == evidence(2, wide=True)["row"]["summary"] for row in rows)


@pytest.mark.parametrize("chunk", [1, 2, 4])
def test_uncommitted_snapshot_keeps_order_and_repeated_positions_see_prior_writes(database, make_case, monkeypatch, chunk):
    cid = make_case("Synthetic repeated ledger position")
    monkeypatch.setattr(source_ledger, "_COMMIT_CHUNK", chunk)
    with database.import_transaction():
        batch = seed(database, cid, [2, 3, 2, 4, 3])
        assert source_ledger.commit(cid, SOURCE, batch) == {"added": 3, "duplicates": 2}
    supports = database.q_raw("select source_row,flow_id,disposition from flow_sources where case_id=? order by source_row", [cid])
    assert [row["source_row"] for row in supports] == [1, 2, 3, 4, 5]
    assert [row["flow_id"] for row in supports] == [1, 2, 1, 3, 2]
    assert [row["disposition"] for row in supports] == ["imported", "imported", "duplicate", "imported", "duplicate"]
    assert database.q_raw("select id,trade_no,amount from flows where case_id=? order by id", [cid]) == [
        {"id": n, "trade_no": f"SYN-{n + 1}", "amount": Decimal("-0.01")} for n in range(1, 4)]


@pytest.mark.parametrize("damage", ["missing-field", "invalid-time"])
def test_invalid_tail_rolls_back_journal_and_all_valid_prefix_writes(database, make_case, monkeypatch, damage):
    cid = make_case("Synthetic invalid ledger tail")
    monkeypatch.setattr(source_ledger, "_COMMIT_CHUNK", 3)

    def corrupt(value):
        if damage == "missing-field":
            del value["row"]["amount"]
        else:
            value["row"]["trade_time"] = "not-a-time"

    with pytest.raises(ValueError):
        with database.import_transaction():
            batch = seed(database, cid, [2, 3, 4, 5, 6], damage=corrupt)
            source_ledger.commit(cid, SOURCE, batch)
    for table in ("flows", "flow_sources", "import_sources", "import_members", "import_records", "batches", "source_ledger_index"):
        assert database.q_raw(f"select count(*) n from {table} where case_id=?", [cid]) == [{"n": 0}]
    # The valid four-row prefix still consumes the same sequence before the
    # outer transaction rolls back, including its pending one-row tail.
    assert database.q_raw("select last_value from duckdb_sequences() where sequence_name='flow_seq'") == [{"last_value": 4}]


def test_empty_snapshot_has_a_complete_zero_row_digest(database, make_case):
    cid = make_case("Synthetic empty ledger snapshot")
    with database.import_transaction():
        assert source_ledger.commit(cid, SOURCE, 1) == {"added": 0, "duplicates": 0}


@pytest.mark.parametrize("fault", ["write", "short-write", "flush", "read", "checksum", "missing-row", "extra-row", "invalid-json", "limit"])
def test_snapshot_io_integrity_failures_roll_back_and_close_private_storage(database, make_case, monkeypatch, fault):
    cid = make_case("Synthetic private ledger snapshot failure")
    monkeypatch.setattr(source_ledger, "_COMMIT_CHUNK", 2)
    temporary, streams, failures = source_ledger.tempfile.TemporaryFile, [], []

    class Fault:
        def __init__(self, stream):
            self.stream, self.writes = stream, 0

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, value):
            self.writes += 1
            if self.writes == 2 and fault == "write":
                failures.append(fault)
                raise OSError("Synthetic ledger snapshot write failure")
            count = self.stream.write(value)
            if self.writes == 2 and fault == "short-write":
                failures.append(fault)
                return count - 1
            return count

        def flush(self):
            if fault == "flush":
                failures.append(fault)
                raise OSError("Synthetic ledger snapshot flush failure")
            self.stream.flush()
            if fault in {"checksum", "missing-row", "extra-row", "invalid-json"}:
                self.stream.seek(0)
                value = self.stream.read()
                if fault == "checksum":
                    value = value.replace(MEMBER_SHA.encode(), ("b" * 64).encode(), 1)
                elif fault == "missing-row":
                    value = value[:value.rfind(b"\n", 0, len(value) - 1) + 1]
                elif fault == "extra-row":
                    value += value[:value.index(b"\n") + 1]
                else:
                    value = b"!" + value[1:]
                self.stream.seek(0)
                self.stream.write(value)
                self.stream.truncate()
                failures.append(fault)

        def __iter__(self):
            for index, value in enumerate(self.stream):
                if fault == "read" and index == 2:
                    failures.append(fault)
                    raise OSError("Synthetic ledger snapshot read failure")
                yield value

    def create(*args, **kwargs):
        assert kwargs["dir"] == database.DATA_DIR
        stream = temporary(*args, **kwargs)
        streams.append(stream)
        if os.name != "nt":
            assert stat.S_IMODE(os.fstat(stream.fileno()).st_mode) == 0o600
        return Fault(stream)

    monkeypatch.setattr(source_ledger.tempfile, "TemporaryFile", create)
    if fault == "limit":
        monkeypatch.setattr(import_staging, "MAX_SPOOL_BYTES", 1)
    error = OSError if fault in {"write", "flush", "read"} else import_validation.IntegrityError
    with pytest.raises(error):
        with database.import_transaction():
            batch = seed(database, cid, [2, 3, 4, 5, 6])
            source_ledger.commit(cid, SOURCE, batch)
    if fault != "limit":
        assert failures == [fault]
    assert len(streams) == 1 and all(stream.closed for stream in streams)
    assert not list(database.DATA_DIR.glob("funds-trace-ledger-*"))
    for table in ("flows", "flow_sources", "import_sources", "import_members", "import_records", "batches", "source_ledger_index"):
        assert database.q_raw(f"select count(*) n from {table} where case_id=?", [cid]) == [{"n": 0}]
