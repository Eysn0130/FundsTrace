"""Bounded source summaries retain the frozen public source DTO contract."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
import csv
import io
import json
import threading

import pytest

from app import db, source_ledger
from app.money import money_decimal
from test_source_ledger import add_source, payload
from test_source_contract_68 import HEADERS

# Frozen Z read pipeline; these helpers do not call the new producer or summary.
validate = source_ledger.validate
_scope = source_ledger._scope
_balance_change = source_ledger._balance_change
_current_pair = source_ledger._current_pair
_profiles = source_ledger._profiles
FIELD = source_ledger.FIELD
UNORIENTED_POLICY = source_ledger.UNORIENTED_POLICY
SOURCE_OBSERVATION_POLICY = source_ledger.SOURCE_OBSERVATION_POLICY


def frozen_records(case_id, ids):
    """Load selected observations and their required source context, per read.

    A complete member is retained when chronology is needed: context records,
    incomparable clocks and equal-time competitors must still block a witness.
    Unoriented/same-entry observations need no preceding financial ledger.
    """
    query = """select r.case_id,r.source_id,r.record_no,r.member_path,r.source_row,
        r.disposition,r.reason_code,r.ledger_evidence,s.flow_id,
        case when r.disposition='cancelled' and r.reason_code='matched_cancellation'
            then json_object('cancellation_role',json_extract_string(r.details_json,'$.cancellation_role'),
                'paired_record_no',json_extract_string(r.details_json,'$.paired_record_no'))
            else '{}' end details_json
        from source_ledger_records r left join flow_sources s
        on s.case_id=r.case_id and s.source_id=r.source_id and s.source_row=r.record_no
        where r.case_id=? and """
    selected = "s.flow_id in (select unnest(?::bigint[]))"
    order = " order by r.source_id,r.member_path,r.source_row"
    rows = db.q_raw(query + selected + order, [case_id, ids])
    members, originals = set(), set()
    for row in rows:
        evidence = row["evidence"] = validate(row.pop("ledger_evidence"))
        if (evidence["direction"] != "unknown" and evidence["kind"] != "context"
                and evidence["source_currency"] and evidence["row"]["balance"] is not None
                and evidence["row"]["trade_time"] and evidence["balance_before"] is None):
            members.add((row["source_id"], row["member_path"]))
        if row["disposition"] == "cancelled" and row["reason_code"] == "matched_cancellation":
            details = json.loads(row["details_json"])
            if details["cancellation_role"] != "original":
                originals.add((row["source_id"], int(details["paired_record_no"])))
    if not members and not originals:
        return rows
    members, originals = sorted(members), sorted(originals)
    # Drop the seed snapshots before the larger context query materializes.
    del rows
    rows = db.q_raw(query + "(" + selected + """ or (r.source_id,r.member_path) in
        (select unnest(?::varchar[]),unnest(?::varchar[])) or (r.source_id,r.record_no) in
        (select unnest(?::varchar[]),unnest(?::bigint[])))""" + order,
        [case_id, ids, [item[0] for item in members], [item[1] for item in members],
         [item[0] for item in originals], [item[1] for item in originals]])
    for row in rows:
        row["evidence"] = validate(row.pop("ledger_evidence"))
    return rows


def frozen_information(case_id, ids):
    wanted = {str(value) for value in ids}
    if not wanted:
        return {}
    indexed = {str(row["flow_id"]) for row in db.q_raw(
        "select flow_id from source_ledger_index where case_id=? and flow_id in (select unnest(?::bigint[]))",
        [case_id, list(map(int, wanted))])}
    if not indexed:
        return {}
    financial = {str(row["id"]) for row in db.q_raw(
        "select id from source_effective_flows where case_id=? and id in (select unnest(?::bigint[]))",
        [case_id, list(map(int, indexed))])}
    visible = indexed - financial
    if not visible:
        return {}
    records = frozen_records(case_id, sorted(map(int, visible)))
    groups = defaultdict(list)
    for record in records:
        if record["evidence"]["direction"] != "unknown":
            groups[_scope(record)].append(record)
    from app.table_cancellation_links import active_support_sql
    members = sorted({(record["source_id"], record["member_path"]) for record in records})
    unlocated = {(row["source_id"], row["member_path"]) for row in db.q_raw(f"""
        select distinct r.source_id,r.member_path from import_records r
        join import_sources p on p.case_id=r.case_id and p.source_id=r.source_id
        where r.case_id=? and (r.source_id,r.member_path) in
          (select unnest(?::varchar[]),unnest(?::varchar[]))
          and p.parser_version in ({_profiles()}) and {active_support_sql('p')}
          and r.disposition='unresolved' and (not contains(r.details_json,'\"{FIELD}\"')
            or json_extract_string(json_extract_string(r.details_json,'$.{FIELD}'),'$.policy') in ('{UNORIENTED_POLICY}','{SOURCE_OBSERVATION_POLICY}'))""",
        [case_id, [item[0] for item in members], [item[1] for item in members]])}
    result = {}
    for record in records:
        fid = str(record["flow_id"])
        if fid not in wanted or fid not in visible or fid in result:
            continue
        e = record["evidence"]
        cancelled = _current_pair(case_id, record, records)
        effect = "paired_cancellation" if cancelled else "no_movement" if money_decimal(e["signed_amount"]) == 0 else "unknown"
        result[fid] = {"policy": e["policy"], "signed_amount": e["signed_amount"],
            "source_direction": e["source_direction"], "direction": e["direction"],
            "economic_effect": effect, "economic_amount": "0.00" if effect != "unknown" else None,
            "reported_status": e["reported_status"], "balance_change": _balance_change(record, groups[_scope(record)],
                unlocated=(record["source_id"], record["member_path"]) in unlocated),
            "self_account": e["row"]["self_account"],
            **{"counter_" + key: e.get("counterparty", {}).get(key, "") for key in ("account", "name", "bank")},
            "source_id": record["source_id"], "member_path": record["member_path"], "source_row": record["source_row"]}
    return result


def frozen_selected_rows(case_id, *, canvas_id=None, account=None):
    selection = "select l.flow_id from source_ledger_index l where l.case_id=?"
    selected_params = [case_id]
    if canvas_id is not None:
        selection += " and exists(select 1 from tracked_flows t where t.case_id=l.case_id and t.canvas_id=? and t.flow_id=l.flow_id)"
        selected_params.append(canvas_id)
    ids = [row["flow_id"] for row in db.q_raw(selection, selected_params)]
    if not ids:
        return []
    query = """select f.* from effective_visible_flows f where f.case_id=?
        and f.id in (select unnest(?::bigint[]))"""
    params = [case_id, ids]
    if account is not None:
        query += " and (f.payer_account=? or f.payee_account=? or f.self_account=? or f.source_counter_id=?)"
        params += [account] * 4
    rows = db.q_raw(query + " order by f.trade_time,f.id", params)
    entries = frozen_information(case_id, [row["id"] for row in rows])
    rows = [row for row in rows if str(row["id"]) in entries]
    for row in rows:
        row["source_ledger"] = entries[str(row["id"])]
    return rows


def frozen_summary(case_id, *, account=None, include_rows=False):
    rows = frozen_selected_rows(case_id, account=account)
    result = {"count": len(rows),
              "unknown_effect_count": sum(row["source_ledger"]["economic_effect"] == "unknown" for row in rows),
              "zero_count": sum(row["source_ledger"]["economic_effect"] == "no_movement" for row in rows),
              "paired_count": sum(row["source_ledger"]["economic_effect"] == "paired_cancellation" for row in rows)}
    if include_rows:
        result["records"] = [{"id": str(row["id"]), "trade_no": row["trade_no"] or "",
            "trade_time": str(row["trade_time"]), "amount": format(money_decimal(row["amount"]), ".2f"),
            "currency": row["currency"] or "", "summary": row["summary"] or "",
            "source_ledger": row["source_ledger"]} for row in rows[-50:]]
        result["has_more"] = len(rows) > 50
    return result


def many_payload(count=60):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(HEADERS)
    for index in range(count):
        stamp = (datetime(2037, 2, 3, 9) + timedelta(seconds=index)).isoformat()
        if index % 5 == 0:
            stamp += '+08:00'
        values = {'交易账号': 'SYN-OWNER-A' if index % 2 == 0 else 'SYN-OWNER-B',
                  '交易卡号': 'SYN-CARD-A' if index % 2 == 0 else 'SYN-CARD-B',
                  '交易方户名': 'SYN owner', '交易时间': stamp, '交易流水号': 'SYN-' + str(index),
                  '交易金额': '0.00' if index % 3 == 0 else '-9999999999999999.99' if index == count - 1 else '-0.01',
                  '交易余额': '1000.00', '收付标志': '出', '交易对手账卡号': 'SYN-COUNTER',
                  '交易币种': ('CNY', 'USD', '')[index % 3], '交易是否成功': '01',
                  '凭证号': 'SYN independent voucher', '摘要说明': 'SYN retained text ' + 'x' * 4096}
        writer.writerow([values.get(header, '') for header in HEADERS])
    return stream.getvalue().encode('utf-8-sig')


def many_case(client, database, make_case, *, repeated=False, paged=False):
    cid = make_case('SYN bounded source stats')
    data = many_payload()
    _, job = add_source(client, cid, data)
    sid = job['result']['source_id']
    if repeated:
        add_source(client, cid, data, 'repeat.csv')
    first, tail = [], []
    if paged:
        first = [-7, 0] + [10 + 2 * i for i in range(4094)]
        tail = [9007199254740993 + 13 * i for i in range(59)] + [9223372036854775807]
        records = database.q_raw('select record_no from import_records where case_id=? and source_id=? order by record_no', [cid, sid])
        with database.transaction() as connection:
            for index, (record, number) in enumerate(zip(records, tail)):
                connection.execute('update import_records set record_no=?,source_row=? where case_id=? and source_id=? and record_no=?',
                                   [number, 500 - index // 2, cid, sid, record['record_no']])
                connection.execute('update flow_sources set source_row=? where case_id=? and source_id=? and source_row=?',
                                   [number, cid, sid, record['record_no']])
            connection.executemany("insert into import_records values (?,?,?,'table.csv',?,'SYN-removed','removed','SYN-removed','{}')",
                                   [(cid, sid, number, 1000 + i) for i, number in enumerate(first)])
    return cid, sid, first, tail


@pytest.mark.parametrize('account', [None, 'SYN-OWNER-A', 'SYN-MISSING'])
@pytest.mark.parametrize('include_rows', [False, True])
def test_complete_summary_dto_matches_frozen_z(client, database, make_case, account, include_rows):
    cid, _, _, _ = many_case(client, database, make_case, repeated=True)
    expected = frozen_summary(cid, account=account, include_rows=include_rows)
    actual = source_ledger.summary(cid, account=account, include_rows=include_rows)
    assert actual == expected
    count, zero = (60, 20) if account is None else (30, 10) if account == 'SYN-OWNER-A' else (0, 0)
    assert {key: actual[key] for key in ('count', 'unknown_effect_count', 'zero_count', 'paired_count')} == {
        'count': count, 'unknown_effect_count': count - zero, 'zero_count': zero, 'paired_count': 0}
    if include_rows:
        assert len(actual['records']) == min(count, 50) and actual['has_more'] is (count > 50)


def test_public_stats_and_tail_details_avoid_casewide_payload_and_unused_balances(client, database, make_case, monkeypatch):
    cid, _, first, tail = many_case(client, database, make_case, paged=True)
    with monkeypatch.context() as old:
        old.setattr(source_ledger, 'summary', frozen_summary)
        expected_legacy = client.get('/api/stats', params={'case_id': cid}).json()
        expected_v1 = client.get(f'/api/v1/cases/{cid}/stats').json()
    expected_details = frozen_summary(cid, include_rows=True)
    original_query, original_rows = database.q_raw, database.raw_rows
    original_information, original_balance = source_ledger.information, source_ledger._balance_change
    payloads, detail_ids, balances = [], [], []
    active = False

    def query(sql, params=None):
        assert not active
        assert 'r.disposition,r.reason_code,r.ledger_evidence' not in sql, 'wide source payload must not use unbounded q_raw'
        return original_query(sql, params)

    @contextmanager
    def rows(sql, params=None):
        nonlocal active
        if 'r.ledger_evidence' not in sql:
            with original_rows(sql, params) as raw:
                yield raw
            return
        assert not active and params[:5] == params[5:]
        assert 0 < len(params[4]) <= 4096 and params[2:4] == [params[4][0], params[4][-1]]
        assert 'r.record_no in (select unnest(?::bigint[]))' in sql
        assert 'source_row in (select unnest(?::bigint[]))' in sql
        state = {'count': 0, 'exhausted': False, 'keys': params[4]}
        with original_rows(sql, params) as raw:
            active = True
            def consume():
                for row in raw:
                    state['count'] += 1
                    yield row
                state['exhausted'] = True
            try:
                yield consume()
            finally:
                active = False
        assert state['exhausted'] and state['count'] <= 4096
        payloads.append(state)

    def information(case_id, ids):
        assert len(ids) <= 50
        detail_ids.append(list(ids))
        return original_information(case_id, ids)

    def balance(*args, **kwargs):
        balances.append(1)
        return original_balance(*args, **kwargs)

    monkeypatch.setattr(database, 'q_raw', query)
    monkeypatch.setattr(database, 'raw_rows', rows)
    monkeypatch.setattr(source_ledger, 'information', information)
    monkeypatch.setattr(source_ledger, '_balance_change', balance)
    assert client.get('/api/stats', params={'case_id': cid}).json() == expected_legacy
    assert client.get(f'/api/v1/cases/{cid}/stats').json() == expected_v1
    assert balances == detail_ids == []
    assert source_ledger.summary(cid, include_rows=True) == expected_details
    assert len(detail_ids) == 1 and len(detail_ids[0]) == len(balances) == 50
    assert [str(value) for value in detail_ids[0]] == [row['id'] for row in expected_details['records']]
    assert payloads and all(not set(first) & set(item['keys']) for item in payloads[:2])
    assert payloads[0]['keys'] == tail


@pytest.mark.parametrize('damage', ['selected-field', 'context-field', 'context-json'])
def test_full_validation_keeps_selected_and_complete_chronology_tail(client, database, make_case, damage):
    cid = make_case('SYN complete source validation')
    _, job = add_source(client, cid, payload())
    sid = job['result']['source_id']
    selected = database.q_raw("select id from flows where case_id=? and amount<0", [cid])[0]['id']
    record = database.q_raw('select * from import_records where case_id=? and source_id=? order by record_no limit 1', [cid, sid])[0]
    details = json.loads(record['details_json'])
    evidence = json.loads(details[FIELD])
    evidence['row']['summary'] = None
    details[FIELD] = json.dumps(evidence)
    if damage == 'selected-field':
        database.x('update import_records set details_json=? where case_id=? and source_id=? and record_no=2',
                   [json.dumps(details), cid, sid])
    else:
        value = '{"source_ledger_evidence":' if damage == 'context-json' else json.dumps(details)
        database.x("insert into import_records values (?,?,9223372036854775807,'table.csv',999,'SYN-tail','unresolved','SYN-tail',?)", [cid, sid, value])
    for read in (lambda: frozen_information(cid, [selected]), lambda: source_ledger.information(cid, [selected]),
                 lambda: frozen_summary(cid, include_rows=True), lambda: source_ledger.summary(cid, include_rows=True)):
        with pytest.raises(Exception):
            read()
    if damage == 'selected-field':
        with pytest.raises(ValueError):
            source_ledger.summary(cid)
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(database.q_raw, 'select 1 n').result(timeout=3) == [{'n': 1}]


@pytest.mark.parametrize('state', ['active', 'missing-original', 'withdrawn'])
def test_cancellation_authority_and_full_dto_keep_frozen_semantics(client, database, make_case, state):
    from test_table_cancellations import round_trip, source
    cid = make_case('SYN current cancellation stats')
    _, package = source(round_trip(), path='table.csv')
    _, proof = add_source(client, cid, package, 'proof.zip')
    if state == 'missing-original':
        database.x('delete from flow_sources where case_id=? and flow_id in (select id from flows where case_id=? and amount=10)',
                   [cid, cid])
    elif state == 'withdrawn':
        assert client.delete(f"/api/batches/{proof['result']['batch_id']}", params={'case_id': cid}).status_code == 200
    for account in (None, 'SYN-A', 'SYN-MISSING'):
        for include_rows in (False, True):
            assert source_ledger.summary(cid, account=account, include_rows=include_rows) == frozen_summary(
                cid, account=account, include_rows=include_rows)
    assert source_ledger.summary(cid)['paired_count'] == (2 if state == 'active' else 0)


@pytest.mark.parametrize('private', [False, True], ids=['shared-lock', 'private-snapshot'])
def test_summary_selection_counts_and_tail_use_one_view(client, database, make_case, monkeypatch, private):
    from app import analysis_snapshot
    cid, sid, _, _ = many_case(client, database, make_case)
    expected = frozen_summary(cid, include_rows=True)
    target = database.q_raw("select r.record_no,r.details_json,f.flow_id from import_records r join flow_sources f on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no join flows v on v.case_id=f.case_id and v.id=f.flow_id where r.case_id=? and r.source_id=? and v.trade_no='SYN-57'", [cid, sid])[0]
    details = json.loads(target['details_json'])
    evidence = json.loads(details[FIELD])
    evidence['signed_amount'] = evidence['row']['amount'] = '-0.01'
    evidence['issues'] = ['negative_amount', 'unknown_status']
    details[FIELD] = json.dumps(evidence)
    started, finished = threading.Event(), threading.Event()
    original, futures = database.raw_rows, []
    dispatched, snapshot_active = False, False

    def write():
        started.set()
        with database.transaction():
            database.x('update import_records set details_json=? where case_id=? and source_id=? and record_no=?',
                       [json.dumps(details), cid, sid, target['record_no']])
            database.x('update flows set amount=-0.01 where case_id=? and id=?', [cid, target['flow_id']])
            analysis_snapshot.invalidate(cid, refresh_source_index=False)
        finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        @contextmanager
        def observe(sql, params=None):
            nonlocal dispatched
            with original(sql, params) as raw:
                yield raw
            if 'r.ledger_evidence' in sql and not dispatched:
                dispatched = True
                futures.append(pool.submit(write))
                assert started.wait(3)
                if snapshot_active:
                    assert finished.wait(3)
                else:
                    assert not finished.wait(0.05)
        monkeypatch.setattr(database, 'raw_rows', observe)
        context = database.read_snapshot() if private else nullcontext(False)
        with context as snapshot_active:
            actual = source_ledger.summary(cid, include_rows=True)
        for future in futures:
            future.result(timeout=3)
    assert dispatched and actual == expected
    assert source_ledger.summary(cid, include_rows=True) != expected


def test_link_multiplicity_and_source_order_keep_full_dto(client, database, make_case, monkeypatch):
    cid, sid, _, _ = many_case(client, database, make_case)
    flow_ids = [row['id'] for row in database.q_raw('select id from flows where case_id=? order by id', [cid])]
    original_query, original_rows = database.q_raw, database.raw_rows

    def repeated(rows):
        rows = list(rows)
        first = next((row for row in rows if row['source_id'] == sid and row['record_no'] == 1), None)
        if first is None:
            return rows
        extra = deepcopy(first)
        extra['flow_id'] = flow_ids[1]
        rows.append(extra)
        rows.sort(key=lambda row: (row['source_id'], row['member_path'], row['source_row']))
        return rows

    def query(sql, params=None):
        rows = original_query(sql, params)
        return repeated(rows) if 'r.disposition,r.reason_code,r.ledger_evidence' in sql else rows

    @contextmanager
    def raw_rows(sql, params=None):
        with original_rows(sql, params) as rows:
            if 'r.ledger_evidence' in sql:
                yield iter(repeated(rows))
            else:
                yield rows

    monkeypatch.setattr(database, 'q_raw', query)
    monkeypatch.setattr(database, 'raw_rows', raw_rows)
    assert source_ledger.summary(cid, include_rows=True) == frozen_summary(cid, include_rows=True)
