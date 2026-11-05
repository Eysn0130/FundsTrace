"""Binding scratch shares the existing disk cap and never owns cached facts."""
import hashlib
from pathlib import Path
import random
import sqlite3

import pytest

from app import import_staging as staging


def path(store):
    return Path(store._con.execute('pragma database_list').fetchone()[2])


@pytest.mark.parametrize('end', ['success', 'exception', 'owner_close'])
def test_working_indexes_leave_plan_bytes_unchanged_and_release_files(database,end):
    store = staging.PlanStore()
    try:
        store.sequence('rows').append({'amount':'10.03','trade_time':'2026-01-01 10:00:00.123456'})
        persistent = store.mapping()
        persistent['original'] = 'checked by the complete cache hash'
        persistent.flush()
        store.flush();store._con.commit()
        before = hashlib.sha256(path(store).read_bytes()).digest()
        try:
            with store.working_maps():
                index = store.mapping()
                index['case-specific'] = ['temporary','lookup']
                assert list(index) == ['case-specific']
                scratch_path = path(index.store)
                assert scratch_path.exists()
                if end == 'exception':
                    raise RuntimeError('Synthetic binding failure')
                if end == 'owner_close':
                    store.close()
        except RuntimeError as error:
            assert str(error) == 'Synthetic binding failure'
        assert not scratch_path.exists()
        with pytest.raises(ValueError):
            index['case-specific']
        if not store._closed:
            store._con.commit()
            assert hashlib.sha256(path(store).read_bytes()).digest() == before
            assert persistent['original'] == 'checked by the complete cache hash'
            assert store._namespace == 1
    finally:
        store.close()
    assert not list(Path(database.DATA_DIR).glob('funds-trace-binding-*'))


@pytest.mark.parametrize('writer', ['plan', 'scratch'])
def test_plan_and_scratch_cannot_each_spend_the_disk_budget(database,monkeypatch,writer):
    store = staging.PlanStore()
    try:
        store.sequence('rows').append({'payload':random.Random(0).randbytes(12000).hex()})
        store.flush();store._con.commit()
        original_limit = store._con.execute('pragma max_page_count').fetchone()[0]
        cap = 96 * 1024
        monkeypatch.setattr(staging,'MAX_SPOOL_BYTES',cap)
        with pytest.raises(sqlite3.OperationalError):
            with store.working_maps():
                scratch = store._working_store
                index = store.mapping()
                index['seed'] = 'x' * 12000
                index.flush()
                try:
                    for number in range(20):
                        if writer == 'scratch':
                            index[number] = 'x' * 12000
                            index.flush()
                        else:
                            store.sequence('rows').append({'payload':random.Random(number).randbytes(12000).hex()})
                finally:
                    assert (store._con.execute('pragma page_count').fetchone()[0] + scratch.pages()) * 4096 <= cap
        assert store._con.execute('pragma max_page_count').fetchone()[0] == original_limit
        assert not list(Path(database.DATA_DIR).glob('funds-trace-binding-*'))
    finally:
        store.close()


def test_insufficient_scratch_creation_budget_cleans_up_without_changing_plan(database,monkeypatch):
    store = staging.PlanStore()
    try:
        store._con.commit()
        before = path(store).read_bytes()
        monkeypatch.setattr(staging,'MAX_SPOOL_BYTES',len(before) + 4096)
        with pytest.raises(sqlite3.OperationalError):
            with store.working_maps():
                pytest.fail('the scratch schema must fit inside the shared cap')
        assert path(store).read_bytes() == before
        assert store._working_store is None
        assert not list(Path(database.DATA_DIR).glob('funds-trace-binding-*'))
    finally:
        store.close()
