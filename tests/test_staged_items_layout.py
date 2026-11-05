"""Physical row identifiers cannot change the staged logical-key contract."""
import copy
import datetime as dt
import sqlite3
from decimal import Decimal

import pytest

from app import import_staging as staging, table_import
from app.services import imports
from test_event_import import csv_rows
from test_folder_evidence import complete, preview, transaction


def test_items_reject_null_and_duplicate_logical_keys(database):
    store = staging.PlanStore()
    try:
        for kind, position in [(None, 1), ('rows', None), (None, None)]:
            with pytest.raises(sqlite3.IntegrityError, match='NOT NULL'):
                store._con.execute('insert into items(kind,position) values (?,?)', (kind, position))
        store._con.execute("insert into items(kind,position) values ('rows',1)")
        with pytest.raises(sqlite3.IntegrityError, match='UNIQUE'):
            store._con.execute("insert into items(kind,position) values ('rows',1)")
    finally:
        store.close()


def test_hidden_rowid_does_not_control_replay_join_mutation_or_deletion(database):
    store = staging.PlanStore();records = store.sequence('records');rows = store.sequence('rows')
    expected = [{'record_no': i, 'member_path': 'synthetic.csv', 'details': {'source': str(i)}} for i in (1, 2, 3)]
    facts = [{'_source_row': i, 'amount': Decimal('10.25'), 'trade_time': dt.datetime(2026, 1, 1, 0, 0, 0, 123456+i)} for i in (1, 2, 3)]
    try:
        records.extend(expected);rows.extend(facts)
        store._con.execute('update items set rowid=1000-rowid*7')
        assert [tuple(row) for row in store._con.execute('select position from items where kind="records" order by rowid')] == [(3,), (2,), (1,)]
        assert copy.deepcopy(list(records)) == expected
        assert copy.deepcopy(list(rows)) == facts
        assert [(row['_source_row'], record['record_no']) for row,record in rows.with_records(records)] == [(1,1),(2,2),(3,3)]
        held = records[2]
        held['details']['source'] = 'late source edit'
        store.flush()
        assert records[2]['details']['source'] == 'late source edit'
        rows.remove_positions([2])
        assert [row['_source_row'] for row in rows] == [1,3]
        assert [(row['_source_row'], record['record_no']) for row,record in rows.with_records(records)] == [(1,1),(3,3)]
        assert records[1]['record_no'] == 2
        rows.append(dict(facts[0], _source_row=2))
        assert [row['_source_row'] for row in rows] == [1,3,2]
    finally:
        store.close()
    with pytest.raises(ValueError, match='closed'):
        held['details']['source'] = 'closed edit'


def test_public_full_staging_page_limit_aborts_and_retry_preserves_original(client, database, make_case, monkeypatch):
    cid = make_case('Synthetic shared staging page quota')
    values = [transaction(**{'交易流水号': f'SYN-{i}'}) for i in range(100)]
    payload = csv_rows([{field: row.get(field, '') for field in sorted(table_import.BANK_FEEDBACK_32_COLUMNS)} for row in values])
    view = preview(client, cid, [('tx.csv', payload)], scope_bank=None)
    bind = imports._bind_table_plan;limits = [];full_errors = []
    flush = staging.StagedMap.flush
    def observed_flush(index):
        try:
            return flush(index)
        except sqlite3.OperationalError as error:
            full_errors.append(error.sqlite_errorcode)
            raise
    def restrict(*args, **kwargs):
        result = bind(*args, **kwargs)
        plan = args[1][0].attrs['table_plan'];store = plan['_plan_store'];store.flush()
        pages = store._con.execute('pragma page_count').fetchone()[0]
        assert store._con.execute(f'pragma max_page_count={pages}').fetchone()[0] == pages
        limits.append(store)
        return result
    with monkeypatch.context() as patch:
        patch.setattr(imports, '_bind_table_plan', restrict)
        patch.setattr(staging.StagedMap, 'flush', observed_flush)
        failed = complete(client, cid, view)
    assert limits and all(store._closed for store in limits)
    assert full_errors == [sqlite3.SQLITE_FULL]
    assert failed['stage'] == 'error' and failed['result'] is None
    for name in ('flows','batches','import_sources','flow_sources','import_members','import_records','import_source_operations','evidence_sources'):
        assert database.q_raw(f'select count(*) n from {name} where case_id=?', [cid]) == [{'n': 0}]
    assert imports._source_available(imports._session(cid, view['import_id']))
    retried = complete(client, cid, view)
    assert retried['stage'] == 'done' and retried['result']['ok'] == 100
