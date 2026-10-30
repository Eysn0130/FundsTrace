"""A request shares selection without narrowing totals or retaining old evidence."""
from decimal import Decimal
import threading

import pytest

from app import analysis_snapshot, evidence_resolution, funds_projection as funds
from app.services import cases
from test_folder_evidence import accepted, account, complete, preview, table, transaction
from test_funds_projection import event, expected, flow, total


@pytest.mark.parametrize('observations', [False, True])
def test_shared_page_keeps_joined_range_deduplication_types_and_empty_totals(database, make_case, observations):
    cid = make_case('Synthetic shared joined selection')
    for number in range(1, 66):
        flow(database, cid, number, '100.00' if number < 3 else f'{number}.01')
    event(database, cid, 'SYN-JOINED-EVENT', [1, 2])
    database.x('update flows set trade_time=null,balance=null where id=65')
    database.x("""insert into resolved_endpoints(case_id,flow_id,role,entity_id,state,reason)
        select ?,id,'payer','SYN-M','resolved','synthetic' from flows where id%2=0""", [cid])
    # Keep the joined BETWEEN shape that exposed DuckDB's window rewrite bug,
    # plus repeated join rows: one original observation is still one case/id.
    source = """select f.* from flows f left join resolved_endpoints e
        on e.case_id=f.case_id and e.flow_id=f.id and e.role='payer'
        cross join (values (1),(2)) copies(n)
        where f.case_id=? and coalesce(e.entity_id,f.payer_account) between 'SYN-A' and 'SYN-Z'"""
    expected_funds = expected(observations=65, confirmed=1, unresolved=63,
        totals=[total('CNY', '100.00', '2242.63')])
    ids = list(range(64, 0, -1)) if observations else [*range(64, 2, -1), 1]
    ids.append(65)
    relation = 'selected_observations' if observations else 'selected_facts'
    for offset in (0, 61, 100):
        rows, summary = funds.page_with_summary(source, [cid], 7, offset, observations=observations)
        assert summary == expected_funds
        assert [row['id'] for row in rows] == ids[offset:offset+7]
        assert all(isinstance(row['amount'], Decimal) for row in rows)
        separate = database.q_raw(funds.cte(source, include_ids=True) +
            f'select * from {relation} order by trade_time desc nulls last,id desc limit 7 offset {offset}', [cid])
        assert rows == separate
    rows, summary = funds.page_with_summary(source, [-1], 7, 0, observations=observations)
    assert rows == [] and summary == expected(observations=0, confirmed=0, unresolved=0, totals=[], status='exact')


def test_public_pages_see_auxiliary_only_write_rollback_withdrawal_and_reopen(client, database, make_case, monkeypatch):
    cid = make_case('Synthetic live page selection')
    source = [('tx.csv', table('transaction', [transaction(**{'交易账号': ''})]))]
    _, first = accepted(client, cid, source)
    def read(account_filter=''):
        response = client.get(f'/api/v1/cases/{cid}/flows', params={'granularity': 'observations', 'account': account_filter})
        assert response.status_code == 200
        return response.json()
    before = read()
    assert before['total'] == 1 and read('SYN-A')['total'] == 0
    _, duplicate = accepted(client, cid, source)
    assert duplicate['ok'] == 0 and duplicate['rows_dup'] == 1
    assert client.delete(f"/api/batches/{first['batch_id']}", params={'case_id': cid}).status_code == 200
    assert read()['items'][0]['id'] == before['items'][0]['id']
    _, auxiliary = accepted(client, cid, [('account.csv', table('account', [account()]))])
    assert auxiliary['ok'] == 0
    linked = read('SYN-A')
    assert linked['total'] == 1 and linked['items'][0]['id'] == before['items'][0]['id']
    assert linked['items'][0]['payer_account'] != before['items'][0]['payer_account']
    assert linked['funds'] == before['funds'] and linked['analysis_revision'] != before['analysis_revision']
    view = preview(client, cid, [('conflict.csv', table('account', [account(number='SYN-OTHER')]))])
    rebuild = evidence_resolution.rebuild
    def failed_rebuild(*args, **kwargs):
        rebuild(*args, **kwargs)
        raise RuntimeError('Synthetic after-resolution rollback')
    with monkeypatch.context() as patch:
        patch.setattr(evidence_resolution, 'rebuild', failed_rebuild)
        failed = complete(client, cid, view)
    assert failed['stage'] == 'error' and failed['result'] is None
    assert read('SYN-A') == linked
    assert client.delete(f"/api/batches/{auxiliary['batch_id']}", params={'case_id': cid}).status_code == 200
    unlinked = read()
    assert unlinked['items'][0]['payer_account'] == before['items'][0]['payer_account']
    assert read('SYN-A')['total'] == 0 and unlinked['funds'] == before['funds']
    package = client.get(f'/api/cases/{cid}/export')
    assert package.status_code == 200
    restored = client.post('/api/cases/import', files={'file': ('synthetic.fundcase', package.content)})
    assert restored.status_code == 200
    copy_id = restored.json()['case_id']
    copied = client.get(f'/api/v1/cases/{copy_id}/flows', params={'granularity': 'observations'}).json()
    assert copied['total'] == 1 and copied['funds'] == unlinked['funds']
    fields = ('amount','currency','balance','trade_time','trade_no','payer_account','payer_display_account','raw_payer_account','event_id','event_state')
    assert {key: copied['items'][0][key] for key in fields} == {key: unlinked['items'][0][key] for key in fields}
    assert client.delete(f"/api/batches/{duplicate['batch_id']}", params={'case_id': cid}).status_code == 200
    assert read()['total'] == 0
    assert client.get(f'/api/v1/cases/{copy_id}/flows').json()['total'] == 1


def test_page_totals_event_metadata_and_revision_hold_one_snapshot(database, make_case, monkeypatch):
    cid = make_case('Synthetic locked page snapshot')
    flow(database, cid, 1, '10.25')
    before_revision = analysis_snapshot.revision(cid)
    started, acquired = threading.Event(), threading.Event()
    original = funds.page_with_summary
    writer = None
    def update():
        started.set()
        with database.transaction():
            acquired.set()
            database.x('update flows set amount=20.50 where case_id=?', [cid])
            analysis_snapshot.invalidate(cid)
    def observed(*args, **kwargs):
        nonlocal writer
        result = original(*args, **kwargs)
        writer = threading.Thread(target=update)
        writer.start()
        assert started.wait(2)
        assert not acquired.wait(.05)
        return result
    with monkeypatch.context() as patch:
        patch.setattr(funds, 'page_with_summary', observed)
        value = cases.flows(str(cid), '', '', 1, 10)
    writer.join(2)
    assert not writer.is_alive() and acquired.is_set()
    assert value['analysis_revision'] == before_revision
    assert value['items'][0]['amount'] == '10.25'
    assert value['items'][0]['analysis_revision'] == before_revision
    assert value['funds']['totals'][0]['upper'] == '10.25'
    fresh = cases.flows(str(cid), '', '', 1, 10)
    assert fresh['analysis_revision'] != before_revision
    assert fresh['items'][0]['amount'] == fresh['funds']['totals'][0]['upper'] == '20.50'


@pytest.mark.parametrize('granularity', ['observations', 'events'])
def test_public_page_keeps_unknown_negative_and_exact_cents_outside_page(client, database, make_case, granularity):
    cid = make_case('Synthetic complete filtered funds')
    flow(database, cid, 1, '90071992547409.91')
    flow(database, cid, 2, '90071992547409.91')
    event(database, cid, 'SYN-LARGE-EVENT', [1, 2], '90071992547409.91')
    flow(database, cid, 3, '0.03', 'USD')
    flow(database, cid, 4, None, None)
    flow(database, cid, 5, '-1.01')
    expected_funds = expected(observations=5, confirmed=1, unresolved=3, status='unknown', totals=[
        total('CNY', '90071992547409.91', None), total('USD', '0.00', None), total(None, '0.00', None)])
    for page, ids in [(1, ['5']), (2, ['4']), (99, [])]:
        response = client.get(f'/api/v1/cases/{cid}/flows', params={'granularity': granularity, 'page': page, 'page_size': 1})
        assert response.status_code == 200
        value = response.json()
        assert value['funds'] == expected_funds and value['total'] == (5 if granularity == 'observations' else 4)
        assert [row['id'] for row in value['items']] == ids
        if page == 1:
            assert value['items'][0]['amount'] == '-1.01'
        elif page == 2:
            assert value['items'][0]['amount'] is None
