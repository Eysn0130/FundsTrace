"""Legacy pages keep the complete frozen public DTO with late payload lookup."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Optional
import threading
import unicodedata

import pytest
from fastapi import HTTPException, Query

from app import analysis_snapshot, db, evidence_resolution, funds_projection, main, transaction_time
from app.money import money_decimal
from test_funds_projection import flow, event


def frozen_legacy_flows(account: str = "", name: str = "", counter: str = "", kw: str = "",
          min_amount: Optional[str] = None, max_amount: Optional[str] = None,
          tfrom: str = "", tto: str = "", ids: str = "", case_id: int = 1,
          tracked: int = 0, canvas_id: int = 0,
          page: int = 1, size: int = Query(50, le=500), account_exact: bool = False,
          counter_exact: bool = False, entity: str = "", granularity: str = "events"):
    try:
        lower = money_decimal(min_amount) if min_amount not in (None, "") else None
        upper = money_decimal(max_amount) if max_amount not in (None, "") else None
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if lower is not None and upper is not None and lower > upper:
        raise HTTPException(422, "金额下限不能超过上限")
    conds, params = ["case_id = ?"], [case_id]
    if ids:
        id_list = [str(int(x)) for x in ids.split(",") if x.strip().isdigit()]
        if id_list:
            conds.append(f"id in ({','.join(id_list)})")
    if tracked:
        # 仅已上图：限定当前画布的追踪集合
        conds.append("id in (select flow_id from tracked_flows where case_id = ? and canvas_id = ?)")
        params += [case_id, canvas_id]

    def like(v):
        return f"%{v.strip()}%"

    if account.strip():
        conds.append("(payer_account = ? or payee_account = ? or self_account = ? or raw_self_account = ?)" if account_exact else """(payer_account ilike ? or payee_account ilike ? or self_account ilike ? or raw_self_account ilike ? or payer_display_account ilike ? or payee_display_account ilike ?
            or exists (select 1 from flow_source_card_aliases c where c.case_id=flows.case_id
                and c.flow_id=flows.id and c.own_card ilike ?)
            or exists (select 1 from resolution_entities e,
                unnest(json_extract_string(e.details_json,'$.cards[*]')) card(value)
                where e.case_id=flows.case_id and e.entity_id in (flows.payer_account,flows.payee_account,flows.self_account)
                  and card.value ilike ?)
            or exists (select 1 from resolution_entities e where e.case_id=flows.case_id
                and e.entity_id in (flows.payer_account,flows.payee_account,flows.self_account) and e.account_number ilike ?))""")
        params += [account.strip() if account_exact else like(account)] * (4 if account_exact else 9)
        conds[-1] = "(" + conds[-1] + (" or source_counter_account = ? or source_counter_id = ?)" if account_exact else " or source_counter_account ilike ? or source_counter_id ilike ?)")
        params += [account.strip() if account_exact else like(account)] * 2
    entity_query = unicodedata.normalize("NFKC", entity).strip().lower()
    if entity_query:
        # A free query searches complete row facts, independently of the bounded
        # suggestion pages. Identity is attached only to that case/account key.
        conds.append("""(
            contains(lower(coalesce(payer_account,'')), ?) or contains(lower(coalesce(payee_account,'')), ?)
            or contains(lower(coalesce(payer_name,'')), ?) or contains(lower(coalesce(payee_name,'')), ?)
            or contains(lower(coalesce(payer_display_account,'')), ?) or contains(lower(coalesce(payee_display_account,'')), ?)
            or exists (select 1 from flow_source_card_aliases c where c.case_id=flows.case_id
                and c.flow_id=flows.id and contains(lower(c.own_card), ?))
            or exists (select 1 from resolution_entities e,
                unnest(json_extract_string(e.details_json,'$.cards[*]')) card(value)
                where e.case_id=flows.case_id and e.entity_id in (flows.payer_account,flows.payee_account)
                  and contains(lower(card.value), ?))
            or exists (select 1 from resolution_entities e where e.case_id=flows.case_id
                and e.entity_id in (flows.payer_account,flows.payee_account)
                and contains(lower(e.account_number), ?))
            or exists (select 1 from effective_account_ids identity
                where identity.case_id=flows.case_id
                  and (identity.account=flows.payer_account or identity.account=flows.payee_account)
                  and contains(lower(coalesce(identity.id_no,'')), ?))
        )""")
        params += [entity_query] * 10
    if name.strip():
        conds.append("(payer_name ilike ? or payee_name ilike ?)")
        params += [like(name)] * 2
    if counter.strip():
        if counter_exact:
            conds.append("(payer_account = ? or payee_account = ?)")
            params += [counter.strip()] * 2
        else:
            conds.append("""(payer_name ilike ? or payer_account ilike ? or payee_name ilike ? or payee_account ilike ?
                or exists (select 1 from resolution_entities e,
                    unnest(json_extract_string(e.details_json,'$.cards[*]')) card(value)
                    where e.case_id=flows.case_id and e.entity_id in (flows.payer_account,flows.payee_account)
                      and contains(lower(card.value), ?))
                or exists (select 1 from resolution_entities e where e.case_id=flows.case_id
                    and e.entity_id in (flows.payer_account,flows.payee_account)
                    and contains(lower(e.account_number), ?))
                or exists (select 1 from effective_account_ids identity
                    where identity.case_id=flows.case_id
                      and (identity.account=flows.payer_account or identity.account=flows.payee_account)
                      and contains(lower(coalesce(identity.id_no,'')), ?)))""")
            params += [like(counter)] * 4 + [unicodedata.normalize("NFKC", counter).strip().lower()] * 3
        conds[-1] = "(" + conds[-1] + (" or source_counter_account = ? or source_counter_id = ?)" if counter_exact else " or source_counter_account ilike ? or source_counter_id ilike ?)")
        params += [counter.strip() if counter_exact else like(counter)] * 2
    if kw.strip():
        conds.append("(summary ilike ? or trade_no ilike ?)")
        params += [like(kw)] * 2
    if lower is not None:
        conds.append("amount >= ?")
        params.append(lower)
    if upper is not None:
        conds.append("amount <= ?")
        params.append(upper)
    try:
        time_clause, time_params, time_filter_basis = transaction_time.window(tfrom, tto)
    except (ValueError, TypeError):
        raise HTTPException(422, "时间格式无效，或时间边界混用了来源壁钟与显式偏移") from None
    from app import analysis_snapshot
    with analysis_snapshot.case_read_snapshot(case_id):
        if time_filter_basis == "utc" and not db.q_raw(
                "select 1 from effective_visible_flows as flows where " + " and ".join(conds) + " and trade_time_utc is not null limit 1", params):
            raise HTTPException(422, {"code": "time_basis_incomparable", "message": "当前范围没有可与UTC窗口比较的显式时区记录"})
        if time_clause:
            conds.append(time_clause)
            params.extend(time_params)
        where = "where " + " and ".join(conds)
        # Keep identity filters bound to this case and committed snapshot.
        from app import funds_projection
        if granularity not in {"events", "observations"}:
            raise HTTPException(422, "无效的明细粒度")
        source = f"select * from effective_visible_flows as flows {where}"
        prefix = funds_projection.cte(source, include_ids=True)
        relation = "selected_observations" if granularity == "observations" else "ordinary_facts"
        count_source = f"select case_id,id,amount,currency,balance from ({source}) as visible_source"
        count_prefix = funds_projection.cte(count_source)
        total = db.q(count_prefix + f"select count(*) c from {relation}", params)[0]["c"]
        off = (page - 1) * size
        rows = db.q(prefix + f"select * from {relation} order by {transaction_time.sql_order()} limit ? offset ?",
                    params + [size, off])
        from app import source_ledger
        funds = funds_projection.summary(source_ledger.financial_source(source), params)
        resolution = evidence_resolution.summary(case_id)
        from app import analysis_snapshot, event_resolution
        revision = analysis_snapshot.revision(case_id)
        memberships = {item["flow_id"]: item for item in event_resolution.members(case_id, [row["id"] for row in rows])}
        for row in rows:
            member = memberships.get(str(row["id"]), {})
            row.update(event_id=member.get("event_id"), event_state=member.get("status", "unknown"),
                       observation_ids=row.get("observation_ids", [str(row["id"])]),
                       granularity="observation" if granularity == "observations" else "confirmed_event" if row["fact_status"] == "confirmed" else "unresolved_observation")
        events = event_resolution.summary(case_id)
        from app import source_ledger
        source_ledger.decorate(case_id, rows)
        return {"total": total, "page": page, "size": size, "rows": rows, "resolution": resolution,
                "analysis_revision": revision, "case_event_summary": events, "funds": funds, "time_filter_basis": time_filter_basis, "time_ordering": "within_basis"}


def legacy_route():
    return next(route for route in main.app.routes if getattr(route, 'path', None) == '/api/flows'
                and 'GET' in route.methods)


def frozen_response(client, monkeypatch, params):
    with monkeypatch.context() as old:
        old.setattr(legacy_route().dependant, 'call', frozen_legacy_flows)
        return client.get('/api/flows', params=params)


def page_case(database, make_case):
    cid, foreign = make_case('SYN legacy bounded page'), make_case('SYN foreign legacy page')
    huge = 9007199254740993
    for number, amount, currency in ((1, '100.03', 'CNY'), (2, '100.03', 'CNY'),
            (huge, '9999999999999999.99', 'CNY'), (4, None, None),
            (5, '-1.01', 'CNY'), (6, '0.00', 'CNY'), (7, '0.10', 'USD'), (8, '-5.00', 'CNY')):
        flow(database, cid, number, amount, currency, payer='' if number == 4 else 'SYN-A',
             payee='' if number == 4 else 'SYN-B')
    event(database, cid, 'SYN-LEGACY-EVENT', [1, 2], '100.03')
    database.x("""update flows set trade_time=timestamp '2026-01-01 20:00:00.123456',
        trade_time_utc=timestamp '2026-01-01 12:00:00.123456',
        trade_time_original='2026-01-01T20:00:00.123456+08:00',trade_time_offset='+08:00' where case_id=? and id in (1,2)""", [cid])
    database.x('update flows set balance=-0.01 where case_id=? and id=?', [cid, huge])
    database.x('update flows set trade_time=null,balance=null where case_id=? and id=4', [cid])
    database.x('insert into source_ledger_index(case_id,flow_id) values (?,8)', [cid])
    for number in (2, 4, huge):
        database.x('insert into tracked_flows(case_id,canvas_id,flow_id) values (?,7,?)', [cid, number])
    flow(database, foreign, 100, '999.99')
    analysis_snapshot.invalidate(cid, refresh_source_index=False)
    return cid, huge


@pytest.mark.parametrize('granularity', ['events', 'observations'])
def test_full_public_dto_matches_frozen_r4_for_filters_and_deep_pages(client, database, make_case, monkeypatch, granularity):
    cid, huge = page_case(database, make_case)
    selections = [{}, {'ids': '1,2'}, {'ids': '2'}, {'ids': '4'},
                  {'min_amount': '0.00', 'max_amount': '0.00'}, {'kw': 'SYN-MISSING'},
                  {'tfrom': '2026-01-01T11:59:59Z', 'tto': '2026-01-01T12:00:01Z'},
                  {'tracked': 1, 'canvas_id': 7}]
    for selection in selections:
        for page in (1, 2, 99):
            params = {'case_id': cid, 'granularity': granularity, 'page': page, 'size': 3, **selection}
            expected = frozen_response(client, monkeypatch, params)
            actual = client.get('/api/flows', params=params)
            assert expected.status_code == actual.status_code == 200, (expected.text, actual.text)
            assert actual.json() == expected.json()
            assert all(row['case_id'] == cid and row['id'] != 100 for row in actual.json()['rows'])
            if page == 99:
                assert actual.json()['rows'] == []
    response = client.get('/api/flows', params={'case_id': cid, 'granularity': granularity, 'size': 50}).json()
    assert response['total'] == (8 if granularity == 'observations' else 7)
    count = response['funds']['observation_count'] if granularity == 'observations' else response['funds']['count']['upper']
    assert response['total'] == count + 1  # Source-ledger rows stay in total.
    indexed = {row['id']: row for row in response['rows']}
    assert indexed[huge]['amount'] == '9999999999999999.99' and indexed[huge]['balance'] == '-0.01'
    assert type(indexed[huge]['id']) is int
    stamp = indexed[2 if granularity == 'observations' else 1]
    assert stamp['trade_time'] == '2026-01-01T20:00:00.123456+08:00'
    assert stamp['trade_time_wall'] == '2026-01-01 20:00:00.123456'
    assert stamp['trade_time_utc'] == '2026-01-01T12:00:00.123456Z' and stamp['trade_time_basis'] == 'utc'
    assert indexed[4]['amount'] is indexed[4]['balance'] is None
    assert indexed[7]['trade_time'] == '2026-01-01 12:00:00'


@pytest.mark.parametrize('granularity', ['events', 'observations'])
def test_payload_lookup_is_after_narrow_page_keys(client, database, make_case, monkeypatch, granularity):
    cid, _ = page_case(database, make_case)
    original, queries = database.q_raw, []

    def query(sql, params=None):
        assert '_funds_source as materialized (select * from effective_visible_flows' not in sql, 'legacy page must not materialize the wide source before pagination'
        queries.append((sql, params))
        return original(sql, params)

    monkeypatch.setattr(database, 'q_raw', query)
    response = client.get('/api/flows', params={'case_id': cid, 'granularity': granularity, 'size': 2})
    assert response.status_code == 200, response.text
    narrow = [(sql, params) for sql, params in queries if '_funds_page_keys as materialized' in sql]
    payload = [(sql, params) for sql, params in queries if sql.startswith('select s.* from (select * from effective_visible_flows')]
    assert len(narrow) == len(payload) == 1
    assert narrow[0][1] == [cid, 2, 0]
    assert payload[0][1] == [cid, *[value for row in response.json()['rows'] for value in (cid, row['id'])]]
    assert payload[0][0].count('(s.case_id=? and s.id=?)') == 2


@pytest.mark.parametrize('private', [False, True], ids=['shared-lock', 'private-snapshot'])
def test_page_totals_wire_funds_and_revision_hold_one_view(client, database, make_case, monkeypatch, private):
    cid, huge = page_case(database, make_case)
    if not private:
        @contextmanager
        def locked(_):
            with database.read_lock():
                yield
        monkeypatch.setattr(analysis_snapshot, 'case_read_snapshot', locked)
    params = {'case_id': cid, 'size': 50, 'granularity': 'events'}
    expected = frozen_response(client, monkeypatch, params).json()
    original = funds_projection.page_with_summary
    started, finished, dispatched, futures = threading.Event(), threading.Event(), False, []

    def write():
        started.set()
        with database.transaction():
            database.x('update flows set amount=0.02 where case_id=? and id=?', [cid, huge])
            analysis_snapshot.invalidate(cid, refresh_source_index=False)
        finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        def page(*args, **kwargs):
            nonlocal dispatched
            value = original(*args, **kwargs)
            if not dispatched:
                dispatched = True
                futures.append(pool.submit(write))
                assert started.wait(3)
                if database.in_read_snapshot():
                    assert finished.wait(3)
                else:
                    assert not finished.wait(0.05)
            return value
        monkeypatch.setattr(funds_projection, 'page_with_summary', page)
        actual = client.get('/api/flows', params=params)
        assert actual.status_code == 200, actual.text
        for future in futures:
            future.result(timeout=3)
    assert dispatched and actual.json() == expected
    fresh = client.get('/api/flows', params=params).json()
    assert fresh['analysis_revision'] != expected['analysis_revision']
    assert next(row for row in fresh['rows'] if row['id'] == huge)['amount'] == '0.02'
