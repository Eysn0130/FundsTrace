"""TOP20 ranks the same proved transactions that its ordinary counts display."""
from datetime import datetime, timedelta
from urllib.parse import quote

import pytest

from test_event_import import csv_rows, transaction
from test_folder_evidence import accepted


def entry(peer, index, event, own, *, amount='100.00'):
    return transaction(own, **{'付款账号': 'SYN-A', '收款账号': peer,
        '交易流水号': f'SYN-PRIMARY-{index}-{event}-{own}', '交易金额': amount,
        '交易时间': (datetime(2026, 1, 1, 10) + timedelta(seconds=index * 2 + event)).isoformat(' '),
        '跨账簿事件参考号': f'SYN-EVENT-{index}-{event}', '记账顺序号': str(index * 2 + event + 1)})


@pytest.mark.parametrize('surface', ['legacy', 'registry'])
def test_top_counterparties_rank_canonical_counts_before_limit_and_keep_conflicts_out(client, make_case, surface):
    cid = make_case('Synthetic TOP20 transaction selection')
    rows = [entry(f'SYN-P{i:02d}', i, event, own)
            for i in range(20) for event in range(2) for own in ('SYN-A', f'SYN-P{i:02d}')]
    rows += [entry('SYN-Z', 30, event, 'SYN-A', amount='1.00') for event in range(3)]
    accepted(client, cid, [('ledger.csv', csv_rows(rows))], scope_bank=None)
    stats = client.get(f'/api/v1/cases/{cid}/stats').json()
    assert stats['flows'] == 43 and stats['amount_totals'] == [{'currency': 'CNY', 'amount': '4003.00'}]
    assert stats['event_summary']['confirmed_events'] == 40
    page = client.get(f'/api/v1/cases/{cid}/flows', params={'granularity': 'observations', 'page_size': 200}).json()
    assert page['total'] == len(page['items']) == 83
    account_key = next(row['payer_account'] for row in page['items'] if row['raw_payer_account'] == 'SYN-A')
    counterparties = {row['raw_payee_account']: row['payee_account'] for row in page['items']}
    assert len(counterparties) == 21

    def profile():
        path = f'/api/accounts/{quote(account_key, safe="")}/profile' if surface == 'legacy' else '/api/account-registry/profile'
        response = client.get(path, params={'case_id': cid, 'account': account_key})
        assert response.status_code == 200, response.text
        return response.json()

    data = profile()
    top = data['top_counterparties']
    assert len(top) == 20 and top[0]['acct'] == counterparties['SYN-Z']
    assert (top[0]['out_cnt'], top[0]['out_amt'], top[0]['out_observation_count']) == (3, '3.00', 3)
    assert all((row['out_cnt'], row['out_amt'], row['out_observation_count']) == (2, '200.00', 4) for row in top[1:])

    accepted(client, cid, [('conflict.csv', csv_rows([entry('SYN-P00', 0, 0, 'SYN-A', amount='101.00')]))], scope_bank=None)
    stats = client.get(f'/api/v1/cases/{cid}/stats').json()
    assert stats['flows'] == 42 and stats['amount_totals'] == [{'currency': 'CNY', 'amount': '3903.00'}]
    assert stats['event_summary']['conflict_entries'] == 3
    assert client.get(f'/api/v1/cases/{cid}/flows', params={'granularity': 'observations'}).json()['total'] == 84
    top = profile()['top_counterparties']
    assert len(top) == 20 and top[0]['acct'] == counterparties['SYN-Z']
    assert counterparties['SYN-P00'] not in {row['acct'] for row in top}
