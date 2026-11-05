"""Card display keeps opaque ledger keys and each source observation intact."""
from test_folder_evidence import accepted, account, entities, table, transaction


def test_tied_cards_keep_ledger_identity_and_source_values_after_package_restore(client, database, make_case):
    cid = make_case('Synthetic ledger display')
    _, receipt = accepted(client, cid, [
        ('tx.csv', table('transaction', [
            transaction(**{'交易卡号': 'SYN-Z-CARD', '交易流水号': 'SYN-T1'}),
            transaction(**{'交易卡号': 'SYN-A-CARD', '交易流水号': 'SYN-T2'}),
        ])),
        ('accounts.csv', table('account', [account(card='SYN-Z-CARD'), account(card='SYN-A-CARD')])),
    ])
    own = next(row for row in entities(client, cid)['entities'] if row['account_number'] == 'SYN-A')
    assert own['display_account'] == 'SYN-A-CARD'
    assert own['display_basis'] == 'tied_transaction_cards'
    assert own['cards'] == ['SYN-A-CARD', 'SYN-Z-CARD']
    before = own['entity_id']
    flows = client.get(f'/api/v1/cases/{cid}/flows', params={'granularity': 'observations'}).json()['items']
    assert {row['payer_account'] for row in flows} == {before}
    assert {row['payer_display_account'] for row in flows} == {'SYN-Z-CARD', 'SYN-A-CARD'}
    for card in own['cards']:
        assert client.get(f'/api/v1/cases/{cid}/flows', params={'account': card}).json()['total'] == 2
        for field in ('account', 'entity', 'counter'):
            assert client.get('/api/flows', params={'case_id': cid, field: card}).json()['total'] == 2
    exported = client.get(f'/api/cases/{cid}/export')
    assert exported.status_code == 200
    restored = client.post('/api/cases/import', files={'file': ('synthetic.fundcase', exported.content)})
    assert restored.status_code == 200, restored.text
    after = next(row for row in entities(client, restored.json()['case_id'])['entities'] if row['account_number'] == 'SYN-A')
    assert (after['entity_id'], after['display_account'], after['cards']) == (before, 'SYN-A-CARD', own['cards'])


def test_transaction_cooccurrence_does_not_invent_card_binding_for_another_time(client, make_case):
    cid = make_case('Synthetic observed relation scope')
    accepted(client, cid, [('tx.csv', table('transaction', [
        transaction(**{'交易流水号': 'SYN-EXPLICIT'}),
        transaction(**{'交易账号': '', '交易时间': '2026-01-02 10:00:00', '交易流水号': 'SYN-MISSING'}),
    ]))])
    rows = client.get(f'/api/v1/cases/{cid}/flows', params={'granularity': 'observations'}).json()['items']
    assert len({row['payer_account'] for row in rows}) == 2
    own = next(row for row in entities(client, cid)['entities'] if row['account_number'] == 'SYN-A')
    assert own['cards'] == ['SYN-C1']  # Explicit row-level observed-with fact is retained.
    assert own['display_account'] == 'SYN-C1'
    carrier = next(row for row in entities(client, cid)['entities'] if row['kind'] == 'card')
    assert carrier['display_account'] == 'SYN-C1'
