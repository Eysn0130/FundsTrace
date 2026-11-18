"""Source eligibility never buffers complete journal payloads in DELIM joins."""
import json

import pytest


def legacy_refresh_index(connection, case_id=None):
    scope, parameters = (" where case_id=?", [case_id]) if case_id is not None else ("", [])
    connection.execute("delete from source_ledger_index" + scope, parameters)
    connection.execute("""insert into source_ledger_index(case_id,flow_id,counter_account,counter_name,counter_bank)
        select f.case_id,f.flow_id,
            max(json_extract_string(r.ledger_evidence,'$.counterparty.account')),
            max(json_extract_string(r.ledger_evidence,'$.counterparty.name')),
            max(json_extract_string(r.ledger_evidence,'$.counterparty.bank'))
        from source_ledger_records r join flow_sources f
          on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no
        where (json_extract_string(r.ledger_evidence,'$.kind')='ledger' or r.disposition='cancelled')""" +
        (" and r.case_id=?" if case_id is not None else "") + " group by f.case_id,f.flow_id", parameters)
    legacy_refresh_cards(connection, case_id)


def legacy_refresh_cards(connection, case_id=None):
    """Keep source-owned card aliases independently of financial deduplication."""
    scope, parameters = (" where case_id=?", [case_id]) if case_id is not None else ("", [])
    connection.execute("delete from flow_source_cards" + scope, parameters)
    connection.execute("""insert into flow_source_cards(case_id,flow_id,role,own_account,own_card)
        select distinct f.case_id,f.flow_id,
            case json_extract_string(r.ledger_evidence,'$.direction') when 'out' then 'payer'
                when 'in' then 'payee' else 'self' end,
            json_extract_string(r.ledger_evidence,'$.row.self_account'),
            json_extract_string(r.ledger_evidence,'$.own_card')
        from source_ledger_records r join flow_sources f
          on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no
        where nullif(json_extract_string(r.ledger_evidence,'$.own_card'),'') is not null
          and nullif(json_extract_string(r.ledger_evidence,'$.row.self_account'),'') is not null""" +
        (" and r.case_id=?" if case_id is not None else ""), parameters)


def card_sources(database, make_case):
    from app import source_ledger, table_import
    cid, foreign = make_case('SYN scoped cards'), make_case('SYN foreign scoped cards')
    batch = database.q_raw("insert into batches(case_id,filename) values (?,'SYN-batch') returning id", [cid])[0]['id']

    def source(index, case, profile, entries, *, active=None, member_case=None):
        sid = f'{index:032x}'
        database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,mapping_json,sheet,parser_version,row_kind)
            values (?,?,'SYN.csv',?,0,'B','{}','',?,'table_record')""", [sid, case, 'a' * 64, profile])
        database.x("insert into import_members values (?,?,'SYN.csv','table',?,0,'utf-8-sig',?)",
                   [case if member_case is None else member_case, sid, 'b' * 64, len(entries)])
        if active is not None:
            database.x('insert into import_source_operations values (?,?,?,?)', [case, sid, batch, active])
        for number, (kind, direction, account, card, flow, policy) in enumerate(entries, 1):
            evidence = {'policy': policy, 'kind': kind, 'direction': direction, 'row': {'self_account': account},
                        'own_card': card, 'SYN-unused-wide': 'x' * 4096}
            database.x("insert into import_records values (?,?,?,'SYN.csv',?,'SYN-key','imported','SYN-reason',?)",
                       [case, sid, number, number + 1000, json.dumps({source_ledger.FIELD: json.dumps(evidence)})])
            if flow is not None:
                database.x("insert into flow_sources values (?,?,?,?,'imported')", [case, sid, number, flow])
        # A physical row number is not the source record support identity.
        database.x("insert into flow_sources values (?,?,1012,777,'imported')", [case, sid])

    policy = source_ledger.POLICY
    entries = [('financial', 'out', 'SYN-A', 'SYN-CA', 42, policy),
               ('financial', 'out', 'SYN-A', 'SYN-CA', 42, policy),
               ('context', 'out', 'SYN-A', 'SYN-CA2', 42, policy),
               ('financial', 'in', 'SYN-B', 'SYN-CB', 42, policy),
               ('ledger', 'unknown', 'SYN-C', 'SYN-CC', 42, policy),
               ('context', None, 'SYN-D', 'SYN-CD', 43, policy),
               ('ledger', 'out', ' ', ' ', 44, policy),
               ('financial', 'out', 'SYN-X', '', 45, policy),
               ('financial', 'in', '', 'SYN-CE', 45, policy),
               ('ledger', 'out', 'SYN-Z', None, 46, policy),
               ('context', 'in', None, 'SYN-CN', 46, policy),
               ('ledger', 'out', 'SYN-UNLINKED', 'SYN-CU', None, policy),
               ('financial', 'out', 'SYN-A', 'SYN-A', 42, policy)]
    source(1, cid, table_import.PROFILE, entries)
    source(2, cid, table_import.PROFILE, [('ledger', 'out', 'SYN-INACTIVE', 'SYN-CI', 80, policy)], active=False)
    source(3, cid, 'SYN-unsupported-profile', [('ledger', 'out', 'SYN-OLD', 'SYN-CO', 81, policy)])
    source(4, cid, table_import.SOURCE_LEDGER_PROFILE,
           [('ledger', 'unknown', 'SYN-UNSUPPORTED', 'SYN-CX', 82, source_ledger.UNORIENTED_POLICY),
            ('ledger', 'unknown', 'SYN-UNSUPPORTED2', 'SYN-CX2', 83, source_ledger.SOURCE_OBSERVATION_POLICY)])
    source(5, cid, table_import.PROFILE,
           [('ledger', 'unknown', 'SYN-NEUTRAL', 'SYN-CN2', 84, source_ledger.SOURCE_OBSERVATION_POLICY)], active=True)
    source(6, foreign, table_import.PROFILE, [('ledger', 'in', 'SYN-FOREIGN', 'SYN-CF', 85, policy)])
    source(7, cid, table_import.PROFILE, [('ledger', 'out', 'SYN-NO-MEMBER', 'SYN-CM', 86, policy)], member_case=foreign)
    return cid, foreign


@pytest.mark.parametrize('scoped', [False, True], ids=['all-cases', 'one-case'])
def test_refresh_cards_keeps_exact_old_view_roles_and_support_scope(database, make_case, scoped):
    from app import source_ledger
    cid, foreign = card_sources(database, make_case)
    connection = database.con()
    legacy_refresh_cards(connection)
    database.x("insert into flow_source_cards values (?,9999,'self','SYN-retained','SYN-retained-card')", [foreign])
    target = cid if scoped else None
    legacy_refresh_cards(connection, target)
    expected = database.q_raw('select * from flow_source_cards order by case_id,flow_id,role,own_account,own_card')
    source_ledger.refresh_cards(connection, target)
    assert database.q_raw('select * from flow_source_cards order by case_id,flow_id,role,own_account,own_card') == expected
    current = {(r['flow_id'], r['role'], r['own_account'], r['own_card']) for r in expected if r['case_id'] == cid}
    assert current == {(42, 'payer', 'SYN-A', 'SYN-CA'), (42, 'payer', 'SYN-A', 'SYN-CA2'),
                       (42, 'payer', 'SYN-A', 'SYN-A'), (42, 'payee', 'SYN-B', 'SYN-CB'),
                       (42, 'self', 'SYN-C', 'SYN-CC'), (43, 'self', 'SYN-D', 'SYN-CD'),
                       (44, 'payer', ' ', ' '), (84, 'self', 'SYN-NEUTRAL', 'SYN-CN2')}
    assert any(r['flow_id'] == 9999 for r in expected) is scoped


@pytest.mark.parametrize('scoped', [False, True], ids=['all-cases', 'one-case'])
def test_refresh_index_keeps_complete_old_source_and_card_scope(database, make_case, scoped):
    from app import source_ledger
    cid, foreign = card_sources(database, make_case)
    legacy_refresh_index(database.con())
    database.x("insert into source_ledger_index values (?,9999,'SYN-retained','SYN-name','SYN-bank')", [foreign])
    database.x("insert into flow_source_cards values (?,9999,'self','SYN-retained','SYN-card')", [foreign])
    target = cid if scoped else None
    legacy_refresh_index(database.con(), target)
    expected = {table: database.q_raw(f'select * from {table} order by 1,2,3,4,5')
                for table in ('source_ledger_index', 'flow_source_cards')}
    source_ledger.refresh_index(database.con(), target)
    assert {table: database.q_raw(f'select * from {table} order by 1,2,3,4,5') for table in expected} == expected
    assert [(row['flow_id'], row['counter_account'], row['counter_name'], row['counter_bank'])
            for row in expected['source_ledger_index'] if row['case_id'] == cid] == [
        (42, None, None, None), (44, None, None, None), (46, None, None, None), (84, None, None, None)]
    assert any(row['flow_id'] == 9999 for row in expected['source_ledger_index']) is scoped


def paged_card_source(database, make_case):
    from app import source_ledger, table_import
    cid = make_case('SYN paged source cards')
    sid = '8' * 32
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,mapping_json,sheet,parser_version)
        values (?,?,'SYN.csv',?,0,'B','{}','',?)""", [sid, cid, 'a' * 64, table_import.PROFILE])
    database.x("insert into import_members values (?,?,'SYN.csv','table',?,0,'utf-8-sig',4099)",
               [cid, sid, 'b' * 64])
    first = [-7, 0] + [10 + 2 * index for index in range(4094)]
    tail = [9007199254740993, 9007199254741053, 9223372036854775807]
    evidence = {'policy': source_ledger.POLICY, 'kind': 'financial', 'direction': 'out',
                'row': {'self_account': 'SYN-A'}, 'own_card': 'SYN-card'}
    details = json.dumps({source_ledger.FIELD: json.dumps(evidence)})
    rows = [(cid, sid, number, '{}', 'imported') for number in first]
    rows += [(cid, sid, number, details, 'removed' if number == tail[-1] else 'imported') for number in tail]
    with database.transaction() as connection:
        connection.executemany("""insert into import_records values
            (?,?,?,'SYN.csv',27,'SYN-key',?,'SYN-reason',?)""",
            [(case, source, number, disposition, value) for case, source, number, value, disposition in rows])
        connection.executemany("insert into flow_sources values (?,?,?,?, 'imported')",
                               [(cid, sid, number, 42 if number != tail[-1] else 43) for number in tail])
    # The next source repeats a complete projection key, in a later INSERT page.
    repeated = '9' * 32
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,mapping_json,sheet,parser_version)
        values (?,?,'SYN-repeat.csv',?,0,'B','{}','',?)""", [repeated, cid, 'c' * 64, table_import.PROFILE])
    database.x("insert into import_members values (?,?,'SYN-repeat.csv','table',?,0,'utf-8-sig',1)",
               [cid, repeated, 'd' * 64])
    database.x("insert into import_records values (?,?,0,'SYN-repeat.csv',99,'SYN-key','imported','SYN-reason',?)",
               [cid, repeated, details])
    database.x("insert into flow_sources values (?,?,0,42,'imported')", [cid, repeated])
    return cid, sid, first, tail, details


def test_refresh_cards_pages_empty_matches_nonpositive_gaps_and_global_distinct(database, make_case):
    from app import source_ledger
    cid, sid, first, tail, _ = paged_card_source(database, make_case)
    legacy_refresh_cards(database.con(), cid)
    expected = database.q_raw('select * from flow_source_cards order by 1,2,3,4,5')
    statements = []

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, params=None):
            statements.append((sql, params))
            return self.connection.execute(sql, params or [])

    with database.import_transaction() as connection:
        source_ledger.refresh_cards(Connection(connection), cid)
    assert database.q_raw('select * from flow_source_cards order by 1,2,3,4,5') == expected
    assert [(row['flow_id'], row['role'], row['own_account'], row['own_card']) for row in expected] == [
        (42, 'payer', 'SYN-A', 'SYN-card'), (43, 'payer', 'SYN-A', 'SYN-card')]
    pages = [(sql, params) for sql, params in statements if sql.lstrip().startswith('insert into flow_source_cards')]
    assert len(pages) == 3
    assert [params[4] for _, params in pages[:2]] == [first, tail]
    assert all(0 < len(params[4]) <= 4096 for _, params in pages)
    assert all(params[:2] == params[5:7] and params[2:4] == params[7:9] for _, params in pages)
    assert all(params[2:4] == [params[4][0], params[4][-1]] for _, params in pages)
    assert all('r.record_no in (select unnest(?::bigint[]))' in sql and
               'f.source_row between ?::bigint and ?::bigint' in sql for sql, _ in pages)
    assert sum(sql.lstrip().startswith('delete from flow_source_cards') for sql, _ in statements) == 1
    key_queries = [(sql, params) for sql, params in statements if 'limit 4096' in sql]
    assert [params[2:] for _, params in key_queries if params[:2] == [cid, sid]] == [
        [None, None], [first[-1], first[-1]], [tail[-1], tail[-1]]]


def test_refresh_index_pages_merge_independent_maxima_and_nulls_exactly(database, make_case):
    from app import source_ledger
    cid, sid, first, tail, _ = paged_card_source(database, make_case)
    updates = [(sid, first[0], 'ledger', 'imported', None, 'Z', ''),
               (sid, first[1], 'ledger', 'imported', None, 'z', 'B'),
               (sid, tail[0], 'ledger', 'imported', 'B', None, 'c'),
               (sid, tail[1], 'financial', 'imported', 'SYN-excluded', 'zzzz', 'zzzz'),
               (sid, tail[2], 'ledger', 'removed', None, None, ''),
               ('9' * 32, 0, 'financial', 'cancelled', 'Ω', 'a', None)]
    for source, number, kind, disposition, account, name, bank in updates:
        evidence = {'policy': source_ledger.POLICY, 'kind': kind, 'direction': 'out',
                    'row': {'self_account': 'SYN-A'}, 'own_card': 'SYN-card',
                    'counterparty': {'account': account, 'name': name, 'bank': bank}}
        database.x('update import_records set details_json=?,disposition=? where case_id=? and source_id=? and record_no=?',
                   [json.dumps({source_ledger.FIELD: json.dumps(evidence)}), disposition, cid, source, number])
    for number in first[:2]:
        database.x("insert into flow_sources values (?,?,?,?, 'imported')", [cid, sid, number, 42])
    legacy_refresh_index(database.con(), cid)
    expected = {table: database.q_raw(f'select * from {table} order by 1,2,3,4,5')
                for table in ('source_ledger_index', 'flow_source_cards')}
    statements = []

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, params=None):
            if sql.lstrip().startswith('insert into source_ledger_index'):
                statements.append((sql, params))
            return self.connection.execute(sql, params or [])

    with database.import_transaction() as connection:
        source_ledger.refresh_index(Connection(connection), cid)
        plans = [json.loads(connection.execute('explain (format json) ' + sql, params).fetchone()[1])
                 for sql, params in statements]
    assert {table: database.q_raw(f'select * from {table} order by 1,2,3,4,5') for table in expected} == expected
    assert [(r['flow_id'], r['counter_account'], r['counter_name'], r['counter_bank'])
            for r in expected['source_ledger_index']] == [(42, 'Ω', 'z', 'c'), (43, None, None, '')]
    assert len(statements) == 3
    assert [params[4] for _, params in statements[:2]] == [first, tail]
    assert all(0 < len(params[4]) <= 4096 for _, params in statements)
    assert all('on conflict(case_id,flow_id) do update' in sql for sql, _ in statements)

    def walk(nodes):
        for node in nodes:
            yield node
            yield from walk(node.get('children', []))

    for plan, (sql, params) in zip(plans, statements):
        bounded = next(n for n in walk(plan) if n.get('extra_info', {}).get('CTE Name') == '_index_inputs')
        assert bounded['children'][0]['extra_info']['Projections'] == [
            'case_id', 'source_id', 'record_no', 'counter_account', 'counter_name', 'counter_bank']
        assert not any(str(n.get('extra_info', {}).get('Table', '')).endswith('flow_sources')
                       for n in walk(bounded['children'][:1]))
        assert any(n['name'] == 'CTE_SCAN' for n in walk(bounded['children'][1:]))
        assert "json_extract_string(r.ledger_evidence,'$.kind')='ledger' or r.disposition='cancelled'" in sql
        assert 'ledger_evidence' not in sql.split('select f.case_id,f.flow_id', 1)[1]
        assert params[:2] == params[5:7] and params[2:4] == params[7:9]


def test_refresh_cards_plan_materializes_case_and_scalar_fields_before_flow_join(database, make_case):
    from app import source_ledger
    cid, _ = card_sources(database, make_case)
    statements = []

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, params=None):
            if sql.lstrip().startswith('insert into flow_source_cards'):
                statements.append((sql, params))
            return self.connection.execute(sql, params or [])

    with database.import_transaction() as connection:
        legacy_refresh_cards(Connection(connection), cid)
        source_ledger.refresh_cards(Connection(connection), cid)
        plans = [json.loads(connection.execute('explain (format json) ' + sql, params).fetchone()[1]) for sql, params in statements]

    def walk(nodes):
        for node in nodes:
            yield node
            yield from walk(node.get('children', []))

    assert not any(n.get('extra_info', {}).get('CTE Name') == '_card_inputs' for n in walk(plans[0]))
    for plan, (new_sql, parameters) in zip(plans[1:], statements[1:]):
        card = next(n for n in walk(plan) if n.get('extra_info', {}).get('CTE Name') == '_card_inputs')
        assert card['children'][0]['extra_info']['Projections'] == [
            'case_id', 'source_id', 'record_no', 'direction', 'own_account', 'own_card']
        assert any(n['name'] == 'CTE_SCAN' for n in walk(card.get('children', [])[1:]))
        assert not any(str(n.get('extra_info', {}).get('Table', '')).endswith('flow_sources') for n in walk(card['children'][:1]))
        bounded, joined = new_sql.split('select distinct', 1)
        assert 'where r.case_id=? and r.source_id=?' in bounded and parameters[0] == cid
        assert 'r.record_no between ?::bigint and ?::bigint' in bounded
        assert 'r.ledger_evidence' not in joined
        assert 'f.source_row=r.record_no' in joined
        scans = [n['extra_info'] for n in walk(plan) if str(n.get('extra_info', {}).get('Table', '')).endswith('flow_sources')]
        assert len(scans) == 1
        assert 'source_id=' in str(scans[0].get('Filters', ''))


def test_refresh_cards_insert_failure_rolls_back_both_source_indexes(database, make_case):
    from app import source_ledger
    cid, foreign = card_sources(database, make_case)
    source_ledger.refresh_index(database.con())
    before = {table: database.q_raw(f'select * from {table} order by 1,2,3', [])
              for table in ('source_ledger_index', 'flow_source_cards')}

    class FailingConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, params=None):
            if sql.lstrip().startswith('insert into flow_source_cards'):
                assert self.connection.execute('select count(*) from flow_source_cards where case_id=?', [cid]).fetchone()[0] == 0
                raise RuntimeError('SYN card insert failure after delete')
            return self.connection.execute(sql, params or [])

    with pytest.raises(RuntimeError, match='SYN card insert failure after delete'):
        with database.transaction() as connection:
            source_ledger.refresh_index(FailingConnection(connection), cid)
    assert {table: database.q_raw(f'select * from {table} order by 1,2,3', []) for table in before} == before


def test_refresh_cards_late_page_failure_rolls_back_inserted_cards_and_indexes(database, make_case):
    from app import source_ledger
    cid, sid, first, _, details = paged_card_source(database, make_case)
    database.x('update import_records set details_json=? where case_id=? and source_id=? and record_no=?',
               [details, cid, sid, first[0]])
    database.x("insert into flow_sources values (?,?,?,55,'imported')", [cid, sid, first[0]])
    legacy_refresh_cards(database.con())
    database.x("insert into source_ledger_index values (?,999,'SYN-retained','SYN-name','SYN-bank')", [cid])
    before = {table: database.q_raw(f'select * from {table} order by 1,2,3,4,5')
              for table in ('source_ledger_index', 'flow_source_cards')}

    class FailingConnection:
        def __init__(self, connection):
            self.connection, self.pages = connection, 0

        def execute(self, sql, params=None):
            if sql.lstrip().startswith('insert into flow_source_cards'):
                self.pages += 1
                if self.pages == 2:
                    assert self.connection.execute('select flow_id from flow_source_cards where case_id=?', [cid]).fetchall() == [(55,)]
                    raise RuntimeError('SYN late card page failure')
            return self.connection.execute(sql, params or [])

    with pytest.raises(RuntimeError, match='SYN late card page failure'):
        with database.import_transaction() as connection:
            source_ledger.refresh_index(FailingConnection(connection), cid)
    assert {table: database.q_raw(f'select * from {table} order by 1,2,3,4,5') for table in before} == before


def test_refresh_index_late_page_failure_restores_previous_indexes_and_cards(database, make_case):
    from app import source_ledger
    cid, sid, first, _, details = paged_card_source(database, make_case)
    evidence = json.loads(json.loads(details)[source_ledger.FIELD])
    evidence['kind'], evidence['counterparty'] = 'ledger', {'account': 'SYN-page-one'}
    database.x('update import_records set details_json=? where case_id=? and source_id=? and record_no=?',
               [json.dumps({source_ledger.FIELD: json.dumps(evidence)}), cid, sid, first[0]])
    database.x("insert into flow_sources values (?,?,?,55,'imported')", [cid, sid, first[0]])
    legacy_refresh_index(database.con())
    database.x("insert into source_ledger_index values (?,999,'SYN-retained','SYN-name','SYN-bank')", [cid])
    before = {table: database.q_raw(f'select * from {table} order by 1,2,3,4,5')
              for table in ('source_ledger_index', 'flow_source_cards')}

    class FailingConnection:
        def __init__(self, connection):
            self.connection, self.pages = connection, 0

        def execute(self, sql, params=None):
            if sql.lstrip().startswith('insert into source_ledger_index'):
                self.pages += 1
                if self.pages == 2:
                    assert self.connection.execute('select flow_id,counter_account from source_ledger_index where case_id=?', [cid]).fetchall() == [(55, 'SYN-page-one')]
                    raise RuntimeError('SYN late index page failure')
            return self.connection.execute(sql, params or [])

    with pytest.raises(RuntimeError, match='SYN late index page failure'):
        with database.import_transaction() as connection:
            source_ledger.refresh_index(FailingConnection(connection), cid)
    assert {table: database.q_raw(f'select * from {table} order by 1,2,3,4,5') for table in before} == before


def test_refresh_cards_without_sources_clears_only_selected_case(database, make_case):
    from app import source_ledger
    cid, foreign = make_case('SYN empty cards'), make_case('SYN foreign empty cards')
    for case in (cid, foreign):
        database.x("insert into flow_source_cards values (?,42,'self','SYN-A','SYN-card')", [case])
    with database.import_transaction() as connection:
        source_ledger.refresh_cards(connection, cid)
    assert database.q_raw('select case_id from flow_source_cards') == [{'case_id': foreign}]
    with database.import_transaction() as connection:
        source_ledger.refresh_cards(connection)
    assert database.q_raw('select * from flow_source_cards') == []


def test_active_source_lookup_does_not_cache_wide_journal_rows(client, database, make_case):
    from test_archive_auxiliary_rows import source
    from test_import_report import complete

    cid = make_case('Synthetic narrow source eligibility')
    _, view = complete(client, cid, source('evidence', malformed=False), 'synthetic.zip')
    with database.import_transaction() as connection:
        raw = connection.execute('''explain (format json)
            select record_no,member_path,member_sha256,source_row,disposition,ledger_evidence
            from source_ledger_records where case_id=? and source_id=? order by record_no''',
                                 [cid, view['import_id']]).fetchone()[1]
    plan = json.loads(raw)

    def walk(nodes):
        for node in nodes:
            yield node
            yield from walk(node.get('children', []))

    wide_cache = []
    for node in walk(plan):
        if 'DELIM_JOIN' not in node['name'] or not node.get('children'):
            continue
        cached = node['children'][:1] if node['name'].startswith('LEFT_') else node['children'][1:2]
        for child in walk(cached):
            info = child.get('extra_info', {})
            if str(info.get('Table', '')).endswith('import_records') and 'details_json' in info.get('Projections', []):
                wide_cache.append(node['name'])
    assert wide_cache == []
