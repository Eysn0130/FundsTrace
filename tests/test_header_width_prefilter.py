"""Version-scoped necessary-condition shortcut; all inputs are synthetic."""
import csv
import io
from decimal import Decimal

import pytest

from app import evidence_resolution, event_resolution, table_archive, table_import as t, table_mapping, table_time_formats

PAIRS = [(t.MICROSECOND_ARCHIVE_PROFILE, t.HEADER_WIDTH_ARCHIVE_PROFILE),
         (t.MICROSECOND_XLS_ARCHIVE_PROFILE, t.HEADER_WIDTH_XLS_ARCHIVE_PROFILE)]
HEAD = ['date', 'payer_account', 'payee_account', 'amount', 'balance']
ROW = ['2026-01-01 10:00:00.123456', '0001', '0002', '90071992547409.93', '']


def csv_bytes(rows, delimiter=','):
    output = io.StringIO(newline='')
    csv.writer(output, delimiter=delimiter).writerows(rows)
    return output.getvalue().encode('utf-8-sig')


def parse(raw, profile):
    return t.parse(raw, suffix='.csv', profile=profile, reference_policy=t.TRANSACTION_REFERENCE_POLICY)


@pytest.mark.parametrize('old,new', PAIRS)
def test_only_width_capability_is_added_to_inherited_profile(old, new):
    for name, values in vars(t).items():
        if name.endswith('_PROFILES') and isinstance(values, frozenset) and name != 'HEADER_WIDTH_PROFILES':
            assert (new in values) == (old in values), name
    for name in ('SUPPORTED_PROFILES', 'EVIDENCE_PROFILES', 'REFERENCE_PROFILES',
                 'CANCELLATION_PROFILES', 'XLS_EVIDENCE_PROFILES'):
        assert (new in getattr(table_archive, name)) == (old in getattr(table_archive, name)), name
    assert t.HEADER_WIDTH_PROFILES == {'funds-trace-table-archive-v10', 'funds-trace-table-archive-v11',
                                       'funds-trace-table-archive-v13', 'funds-trace-table-archive-v14', 'funds-trace-table-archive-v16', 'funds-trace-table-archive-v17', 'funds-trace-table-archive-v19', 'funds-trace-table-archive-v20', 'funds-trace-table-archive-v22', 'funds-trace-table-archive-v23', 'funds-trace-table-archive-v25', 'funds-trace-table-archive-v26', 'funds-trace-table-archive-v28', 'funds-trace-table-archive-v29', *{f'funds-trace-table-archive-v{v}' for v in (31, 32, 34, 35, 37, 38, 40, 41, 43, 44, 46, 47, 49, 50, 52, 53, 55, 56, 58, 59)}}
    assert new in evidence_resolution.PROFILES
    assert new in event_resolution.PROFILES
    assert t.PROFILE == 'funds-trace-table-v69'
    assert table_archive.PROFILE == 'funds-trace-table-archive-v57'
    assert set(table_archive.PROFILE_PRECEDENCE) == table_archive.SUPPORTED_PROFILES
    assert table_archive.PROFILE_PRECEDENCE == tuple(f'funds-trace-table-archive-v{v}' for v in range(59, 0, -1))


def test_single_cell_cannot_supply_time_and_money_even_after_alias_or_override_changes():
    # Each candidate branch uses one final normalized base. A new overlapping
    # alias must fail this check even if the shortcut would hide it at runtime.
    vocabularies = (t._ALIASES, t._FINANCIAL_ALIASES, t._CONTRACT_ALIASES)
    times = set().union(*(v.get('trade_time', set()) for v in vocabularies))
    money = set().union(*(v.get(k, set()) for v in vocabularies for k in ('amount', 'income_amt', 'expense_amt')))
    clocks = t._ALIASES['time_part'] | t._FINANCIAL_ALIASES.get('time_part', set()) | t._CLOCK_HEADERS
    assert not (times | clocks) & money
    assert not set().union(*table_mapping._IDENTITIES.values()) & {'trade_time', 'amount', 'income_amt', 'expense_amt'}
    words = set().union(*(set().union(*v.values()) for v in vocabularies))
    words |= {'姓名(查询条件)', '交易对方账号开户行', '币种代码', 'date,amount', 'ｄａｔｅ', ''}
    words |= {word + '(' + spec + ')' for word in times | clocks for spec in table_time_formats._FORMATS}
    words |= {word + '(' + unit + ')' for word in money for unit in (*t._UNIT, 'BAD')}
    for profile in t.HEADER_WIDTH_PROFILES:
        for word in words:
            candidates = t._candidates([word], profile)
            assert not (candidates.get('trade_time') and any(candidates.get(k) for k in ('amount', 'income_amt', 'expense_amt'))), (profile, word)


@pytest.mark.parametrize('profile', sorted(t.HEADER_WIDTH_PROFILES))
def test_only_proved_rows_skip_candidates_and_indices_are_preserved(monkeypatch, profile):
    original = t._candidates
    calls = []
    def observed(row, selected):
        calls.append(row)
        return original(row, selected)
    monkeypatch.setattr(t, '_candidates', observed)
    assert t._locate_header([[], ['note'], ('date',), HEAD], profile)[0] == 3
    assert calls == [HEAD]
    class NoLen(list):
        def __len__(self):
            raise AssertionError('must not inspect custom row length')
    class String(str):
        pass
    for row in (NoLen(['date']), [String('date')], [None], [0], [t._WorkbookValue('date', formula=True)], iter(['date'])):
        calls.clear()
        assert t._locate_header([row], profile) == (None, {}, None)
        assert len(calls) == 1
    class BadCell:
        def __str__(self):
            raise ValueError('synthetic conversion failure')
    with pytest.raises(ValueError, match='synthetic conversion failure'):
        t._locate_header([[BadCell()]], profile)
    with pytest.raises(TypeError):
        t._locate_header([None], profile)
    with pytest.raises(TypeError):
        t._locate_header([[]], [])
    assert t._locate_header([], []) == (None, {}, None)
    calls.clear()
    assert t._locate_header([['date']], String(profile)) == (None, {}, None)
    assert len(calls) == 1


def test_all_historical_and_standalone_profiles_keep_original_candidate_path(monkeypatch):
    original = t._candidates
    calls = []
    def observed(row, profile):
        calls.append(profile)
        return original(row, profile)
    monkeypatch.setattr(t, '_candidates', observed)
    profiles = (t.SUPPORTED_PROFILES | table_archive.SUPPORTED_PROFILES) - t.HEADER_WIDTH_PROFILES
    for profile in sorted(profiles):
        calls.clear()
        assert t._locate_header([[], ['date']], profile) == (None, {}, None)
        assert calls == [profile, profile]


@pytest.mark.parametrize('old,new', PAIRS)
@pytest.mark.parametrize('delimiter', [',', ';', '|', '\t'])
def test_late_header_complete_source_and_exact_facts_match_old(old, new, delimiter):
    raw = csv_bytes([['note'], [], HEAD, ROW], delimiter)
    before, after = parse(raw, old), parse(raw, new)
    for field in ('rows', 'records', 'documents', 'members', 'mapping', 'stats'):
        assert before[field] == after[field], field
    assert after['documents'][0]['header_row'] == 3
    assert [r['source_row'] for r in after['records']] == [1, 2, 4]
    row = after['rows'][0]
    assert (row['amount'], row['balance'], row['payer_account'], row['payee_account']) == (Decimal('90071992547409.93'), None, '0001', '0002')
    assert row['trade_time'].microsecond == 123456


@pytest.mark.parametrize('profile', sorted(t.HEADER_WIDTH_PROFILES))
def test_auxiliary_later_transaction_and_reordered_header_are_retained(profile):
    aux = list(t.table_source_roles._ACCOUNT)
    raw = csv_bytes([aux, ['SYN'] * len(aux), ['note'], HEAD, ROW,
                     ['amount', 'date', 'payee_account', 'payer_account', 'balance'],
                     ['2.34', '2026-01-02', '0004', '0003', '0.00']])
    plan = parse(raw, profile)
    assert plan['documents'][0]['header_row'] == 4
    assert [(r['payer_account'], r['amount']) for r in plan['rows']] == [('0001', Decimal('90071992547409.93')), ('0003', Decimal('2.34'))]
    assert [r['source_row'] for r in plan['records'] if r['disposition'] == 'accepted'] == [5, 7]


@pytest.mark.parametrize('profile', sorted(t.HEADER_WIDTH_PROFILES))
def test_four_100_row_probes_still_collect_complete_input(monkeypatch, profile):
    reader = t.csv.reader
    counts = []
    def observed(*args, **kwargs):
        i = len(counts)
        counts.append(0)
        for row in reader(*args, **kwargs):
            counts[i] += 1
            yield row
    monkeypatch.setattr(t.csv, 'reader', observed)
    plan = parse(csv_bytes([['note']] * 101 + [HEAD, ROW]), profile)
    assert counts == ([103] * 4 if profile in t.SOURCE_CONTRACT_PROFILES else [100, 100, 100, 100, 103])
    assert plan['records'][-1]['source_row'] == 103
    assert len(plan['rows']) == 1


@pytest.mark.parametrize('profile', sorted(t.HEADER_WIDTH_PROFILES))
@pytest.mark.parametrize('raw,message', [(b'date,amount\ndate;amount\n', 'CSV分隔符存在歧义'),
                                        (b'date,amount\n"unterminated', 'CSV引号或记录结构损坏')])
def test_bad_files_still_reject(profile, raw, message):
    if profile in t.SOURCE_CONTRACT_PROFILES and raw == b'date,amount\ndate;amount\n':
        # The full-record parser rejects the malformed comma interpretation;
        # the sole complete semicolon interpretation is a header with no facts.
        plan = parse(raw, profile)
        assert plan['rows'] == [] and plan['documents'][0]['header_row'] == 2
        assert plan['records'][0]['disposition'] == 'auxiliary'
        return
    with pytest.raises(t.TableImportError, match=message):
        parse(raw, profile)


@pytest.mark.parametrize('profile', sorted(t.HEADER_WIDTH_PROFILES))
@pytest.mark.parametrize('limit,value,raw,message', [
    ('MAX_COLUMNS', 2, csv_bytes([HEAD, ROW]), '记录数或列数'),
    ('MAX_RECORDS', 2, csv_bytes([HEAD, ROW, ROW]), '记录数或列数'),
    ('MAX_FIELD', 8, csv_bytes([['X' * 9]]), '字段超出'),
])
def test_complete_collection_limits_still_apply(monkeypatch, profile, limit, value, raw, message):
    monkeypatch.setattr(t, limit, value)
    with pytest.raises(t.TableImportError, match=message):
        parse(raw, profile)
