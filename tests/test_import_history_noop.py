"""Empty history does not materialize a large staged comparison index."""
import io
import csv
from datetime import datetime, timedelta
from decimal import Decimal


def test_fresh_standalone_history_preserves_facts_without_decoding_plan_again(client, database, make_case, monkeypatch):
    from app import import_staging
    from app.services import imports
    from test_import_report import complete

    monkeypatch.setattr(import_staging, 'PLAN_CHUNK', 8)
    samples = []
    original = imports._bind_table_history

    def observed(case_id, plan, rows, stats):
        store = plan['_plan_store']
        before = store.decode_calls
        result = original(case_id, plan, rows, stats)
        samples.append(store.decode_calls - before)
        return result

    monkeypatch.setattr(imports, '_bind_table_history', observed)
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['交易时间', '本方账号', '本方户名', '对方账号', '对方户名', '交易金额', '收付标志', '币种'])
    for index in range(257):
        writer.writerow([(datetime(2026, 1, 1) + timedelta(seconds=index)).isoformat(sep=' '),
                         '000SYN-HISTORY-A', '合成户名', '000SYN-HISTORY-B', '合成对手', '1.01', '支出', 'CNY'])
    cid = make_case('Synthetic empty history index')
    complete(client, cid, stream.getvalue().encode('utf-8-sig'))
    assert len(samples) == 2 and samples == [0, 0]
    assert database.q_raw('select count(*) n,sum(amount) amount from flows where case_id=?', [cid]) == [
        {'n': 257, 'amount': Decimal('259.57')}]
    assert database.q_raw('select count(*) n from import_records where case_id=?', [cid])[0]['n'] == 257
    assert database.q_raw('select count(*) n from flow_sources where case_id=?', [cid])[0]['n'] == 257
    first = client.get(f'/api/v1/cases/{cid}/flows', params={'page_size': 1}).json()['items'][0]
    assert first['raw_payer_account'] == '000SYN-HISTORY-A' and first['amount'] == '1.01'
