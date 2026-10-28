"""Raw ZIP names must not acquire authority from ZipInfo's NUL truncation."""
import io
import zipfile

import pytest

from xls_fixtures import workbook


@pytest.mark.parametrize('suffix', ['csv', 'xls'])
def test_original_member_name_is_validated_before_normalized_extension(client, database, make_case, suffix):
    headers = ['date', 'payer_account', 'payee_account', 'amount', 'currency']
    row = ['2026-01-01 10:00:00', 'SYN-A', 'SYN-B', '1.23', 'CNY']
    payload = (','.join(headers) + '\n' + ','.join(row) + '\n').encode() if suffix == 'csv' else workbook([('Synthetic', [headers, row])])
    stream = io.BytesIO()
    name = f'ledger.{suffix}!hidden'
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_STORED) as archive:
        archive.writestr(name, payload)
    data = stream.getvalue()
    assert data.count(name.encode()) == 2
    data = data.replace(name.encode(), name.replace('!', '\0').encode())
    cid = make_case('Synthetic raw member name')
    response = client.post(f'/api/v1/cases/{cid}/imports/preview', files={'file': ('synthetic.zip', data)})
    assert response.status_code == 400
    assert database.q_raw('select count(*) n from import_sessions where case_id=?', [cid])[0]['n'] == 0
    assert database.q_raw('select count(*) n from flows where case_id=?', [cid])[0]['n'] == 0
