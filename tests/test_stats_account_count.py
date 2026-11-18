"""Account counts preserve the frozen ordinary/source endpoint set."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from app import analysis_snapshot, db, funds_projection
from app.services import cases
from test_funds_projection import flow, event


FROZEN_ACCOUNT_SQL = """select count(distinct account) accounts from (
    select payer_account account from ordinary_facts union all
    select payee_account from ordinary_facts union all
    select f.payer_account from effective_visible_flows f join source_ledger_index l
      on l.case_id=f.case_id and l.flow_id=f.id where f.case_id=? union all
    select f.payee_account from effective_visible_flows f join source_ledger_index l
      on l.case_id=f.case_id and l.flow_id=f.id where f.case_id=? union all
    select f.self_account from effective_visible_flows f join source_ledger_index l
      on l.case_id=f.case_id and l.flow_id=f.id where f.case_id=? union all
    select f.source_counter_id from effective_visible_flows f where f.case_id=?
    ) where coalesce(account,'')<>''"""


def frozen_account_count(case_id):
    return db.q_raw(funds_projection.cte('select * from effective_flows where case_id=?') +
                    FROZEN_ACCOUNT_SQL, [case_id] * 5)[0]['accounts']


def account_case(database, make_case, *, ledger=True, nonrepresentative=False):
    cid, foreign = make_case('SYN account set'), make_case('SYN foreign account set')
    flow(database, cid, 1, payer='A', payee='B')
    flow(database, cid, 2, payer='HIDDEN', payee='GHOST')
    # Isolate the projection contract: a nonrepresentative observation must not
    # enlarge ordinary_facts, even if its stored raw endpoints differ.
    event(database, cid, 'SYN-EVENT', [1, 2], payer='A', payee='B')
    if ledger:
        flow(database, cid, 3, '-1.00', payer='LP', payee='LQ')
        database.x("update flows set self_account='LS' where id=3 and case_id=?", [cid])
    flow(database, cid, 4, '1.00', payer=' ', payee='')
    flow(database, cid, 5, '1.00', payer=None, payee='A')
    flow(database, cid, 6, '1.00', payer='A', payee='a')
    flow(database, foreign, 100, payer='FOREIGN-A', payee='FOREIGN-B')
    if ledger:
        database.x('insert into source_ledger_index(case_id,flow_id) values (?,3)', [cid])
    if nonrepresentative:
        database.x('insert into source_ledger_index(case_id,flow_id) values (?,2)', [cid])
    # A foreign index row cannot grant ledger endpoints to this case.
    database.x('insert into source_ledger_index(case_id,flow_id) values (?,2)', [foreign])
    for number, counter in ((2, 'CounterOnly'), (4, ''), (5, 'A')):
        database.x("""insert into resolved_endpoints(case_id,flow_id,role,entity_id,state,reason)
            values (?,?,'counter',?,'resolved','SYN account-set fixture')""", [cid, number, counter])
    analysis_snapshot.invalidate(cid, refresh_source_index=False)
    expected = {'A', 'B', 'CounterOnly', ' ', 'a'}
    if ledger:
        expected.update(('LP', 'LQ', 'LS'))
    if nonrepresentative:
        expected.update(('HIDDEN', 'GHOST'))
    return cid, expected


@pytest.mark.parametrize('ledger,nonrepresentative', [(False, False), (True, False), (True, True)])
def test_exact_set_keeps_ordinary_representative_and_all_visible_counters(database, make_case, ledger, nonrepresentative):
    cid, expected = account_case(database, make_case, ledger=ledger, nonrepresentative=nonrepresentative)
    assert cases.account_count(cid) == frozen_account_count(cid) == len(expected)


def test_empty_case_does_not_count_foreign_accounts(database, make_case):
    cid = make_case('SYN empty account set')
    foreign = make_case('SYN other account set')
    flow(database, foreign, 100, payer='FOREIGN-A', payee='FOREIGN-B')
    assert cases.account_count(cid) == frozen_account_count(cid) == 0


@pytest.mark.parametrize('surface', ['legacy', 'v1'])
def test_full_stats_dto_matches_frozen_account_sql(client, database, make_case, monkeypatch, surface):
    cid, expected_accounts = account_case(database, make_case)
    url = f'/api/stats?case_id={cid}' if surface == 'legacy' else f'/api/v1/cases/{cid}/stats'
    with monkeypatch.context() as old:
        old.setattr(cases, 'account_count', frozen_account_count)
        response = client.get(url)
        assert response.status_code == 200, response.text
        expected = response.json()
    actual = client.get(url)
    assert actual.status_code == 200, actual.text
    assert actual.json() == expected
    assert actual.json()['accounts'] == len(expected_accounts)


def test_visible_accounts_expand_once_and_materialize_only_four_columns(database, make_case, monkeypatch):
    cid, expected = account_case(database, make_case)
    original, statements = database.q_raw, []

    def query(sql, params=None):
        if 'count(distinct account) accounts' in sql and 'effective_visible_flows' in sql:
            statements.append((sql, params))
        return original(sql, params)

    monkeypatch.setattr(database, 'q_raw', query)
    assert cases.account_count(cid) == len(expected)
    assert len(statements) == 1
    sql, params = statements[0]
    assert sql.count('effective_visible_flows') == 1
    assert params == [cid, cid]
    plan = json.loads(original('explain (format json) ' + sql, params)[0]['explain_value'])

    def walk(nodes):
        for node in nodes:
            yield node
            yield from walk(node.get('children', []))

    materialized = [node for node in walk(plan) if node.get('extra_info', {}).get('CTE Name') == '_stats_visible_accounts']
    assert len(materialized) == 1
    projections = materialized[0]['children'][0]['extra_info']['Projections']
    assert projections == ['payer_account', 'payee_account', 'self_account', 'source_counter_id']


@pytest.mark.parametrize('private', [False, True], ids=['shared-lock', 'private-snapshot'])
def test_revision_and_account_set_share_the_caller_view(database, make_case, private):
    cid, expected = account_case(database, make_case)
    started, finished = threading.Event(), threading.Event()

    def write():
        started.set()
        with database.transaction():
            database.x("update flows set payer_account='NEW-A',payee_account='NEW-B' where case_id=? and id=5", [cid])
            analysis_snapshot.invalidate(cid, refresh_source_index=False)
        finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        context = database.read_snapshot() if private else database.read_lock()
        with context:
            before = analysis_snapshot.revision(cid)
            future = pool.submit(write)
            assert started.wait(3)
            if database.in_read_snapshot():
                assert finished.wait(3)
            else:
                assert not finished.wait(0.05)
            assert cases.account_count(cid) == len(expected)
            assert analysis_snapshot.revision(cid) == before
        future.result(timeout=3)
    assert cases.account_count(cid) == len(expected) + 2
    assert analysis_snapshot.revision(cid) != before
