"""Literal Windows acceptance fixtures use public APIs and portable package tables."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import zipfile

import pytest

from test_automatic_table_import import preview
from test_feedback_import_api import complete

ROOT = Path(__file__).absolute().parents[1]
SPEC = importlib.util.spec_from_file_location('windows_import_fixtures', ROOT / 'scripts/windows_import_fixtures.py')
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)


@pytest.fixture
def fixture_dir(tmp_path):
    output = tmp_path / 'format-fixtures'
    fixtures.generate(output)
    return output


def upload(client, cid, directory, file, name=None):
    view = preview(client, cid, (directory / file).read_bytes(), name or file)
    result = complete(client, cid, view)
    assert result['stage'] == 'done'
    return view, result


def public_package(client, cid, tmp_path):
    response = client.get(f'/api/cases/{cid}/export')
    assert response.status_code == 200
    package = tmp_path / 'synthetic.fundcase'
    package.write_bytes(response.content)
    return fixtures.inspect_package(package)


def test_generator_is_literal_reproducible_and_refuses_reuse(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    receipt = fixtures.generate(first)
    fixtures.generate(second)
    assert receipt['versions'] == {'openpyxl': '3.1.5', 'xlwt': '1.3.0', 'duckdb': '1.5.6'}
    for file in receipt['files']:
        assert (first / file['name']).read_bytes() == (second / file['name']).read_bytes()
        assert hashlib.sha256((first / file['name']).read_bytes()).hexdigest() == file['sha256']
    with zipfile.ZipFile(first / 'formats.zip') as package:
        assert package.namelist() == ['bank/container.zip']
        with zipfile.ZipFile(io.BytesIO(package.read('bank/container.zip'))) as nested:
            assert nested.namelist() == ['nested/renamed-transactions.csv']
            assert nested.read(nested.namelist()[0]) == (first / 'formats.csv').read_bytes()
    with pytest.raises(ValueError, match='empty'):
        fixtures.generate(first)


def test_public_four_formats_have_literal_money_identities_and_provenance(client, make_case, fixture_dir, tmp_path):
    cid = make_case('SYN Windows public four formats')
    sources = []
    for index, file in enumerate(('formats.csv', 'formats.xlsx', 'formats.xls', 'formats.zip')):
        view, job = upload(client, cid, fixture_dir, file)
        assert (job['result']['ok'], job['result']['rows_dup']) == ((3, 0) if index == 0 else (0, 3))
        sources.append(view['import_id'])
    package = public_package(client, cid, tmp_path)
    assert package['rawFlows'] == 3
    assert package['rawAmountTotals'] == [{'currency': 'CNY', 'amount': '125.48'}, {'currency': 'USD', 'amount': '4.56'}]
    assert sorted((row['trade_no'], row['payer_account'], row['payee_account'], row['amount'], row['currency'])
                  for row in package['tables']['flows']) == [
        ('SYN-F1', '000SYN-A', '000SYN-B', '123.45', 'CNY'),
        ('SYN-F2', '000SYN-B', '000SYN-A', '2.03', 'CNY'),
        ('SYN-F3', '000SYN-A', '000SYN-C', '4.56', 'USD')]
    for source_id in sources:
        report = client.get(f'/api/v1/cases/{cid}/sources/{source_id}/report').json()
        assert [row['source_row'] for row in report['items']] == [2, 3, 4]
    api_rows = client.get(f'/api/v1/cases/{cid}/flows').json()['items']
    assert sorted((row['trade_no'], row['trade_time']) for row in api_rows) == [
        ('SYN-F1', '2026-01-01 10:00:00'), ('SYN-F2', '2026-01-01 11:00:00'),
        ('SYN-F3', '2026-01-01 12:00:00')]
    assert len(package['tables']['import_sources']) == 4
    assert len(package['tables']['flow_sources']) == 12


@pytest.mark.parametrize('file,reasons', [
    ('regions.csv', ['transaction_valid', 'ambiguous_region_header', 'ambiguous_region_header', 'repeated_header', 'transaction_valid']),
    ('regions.xlsx', ['transaction_valid', 'ambiguous_region_header', 'ambiguous_region_header', 'repeated_header', 'transaction_valid']),
    ('typed-controls.xls', ['transaction_valid', 'formula_value', 'invalid_cell_type']),
])
def test_public_controls_preserve_reasons_and_do_not_poison_neighbors(client, make_case, fixture_dir, file, reasons):
    cid = make_case('SYN Windows public controls')
    view, job = upload(client, cid, fixture_dir, file)
    assert job['result']['ok'] == (1 if file.endswith('.xls') else 2)
    report = client.get(f'/api/v1/cases/{cid}/sources/{view["import_id"]}/report').json()
    assert [row['reason_code'] for row in report['items']] == reasons
    assert sum(row['disposition'] == 'unresolved' for row in report['items']) == 2
    if file.endswith('.xls'):
        assert client.get(f'/api/v1/cases/{cid}/flows').json()['items'][0]['payer_account'] == '00011112222333344445'


@pytest.mark.parametrize('conflict', [False, True])
def test_public_chain_has_independent_literal_raw_and_effective_oracles(client, make_case, fixture_dir, tmp_path, conflict):
    cid = make_case('SYN Windows public cancellation')
    prefix = 'chain-conflict' if conflict else 'chain'
    view = preview(client, cid, (fixture_dir / (prefix + '.csv')).read_bytes(), 'ordinary.csv')
    if conflict:
        result = client.post(f'/api/v1/cases/{cid}/imports/{view["import_id"]}/preview',
                             json={'mode': view['mode'], 'mapping': {**view['mapping'], 'summary': '商户名称'}})
        assert result.status_code == 200
        view = result.json()
    else:
        assert complete(client, cid, view)['stage'] == 'done'
    zipped, _ = upload(client, cid, fixture_dir, prefix + '.zip', 'nested.zip')
    before = client.get(f'/api/v1/cases/{cid}/sources/{zipped["import_id"]}/report').json()
    if conflict:
        assert complete(client, cid, view)['stage'] == 'done'
    assert client.get(f'/api/v1/cases/{cid}/sources/{zipped["import_id"]}/report').json() == before
    stats = client.get(f'/api/v1/cases/{cid}/stats').json()
    assert stats['flows'] is None
    from funds_assertions import unresolved_funds
    assert stats['funds'] == unresolved_funds(4 if conflict else 2, ('CNY', '33.00' if conflict else '3.00'))
    assert stats['accounts'] == 3
    assert stats['amount_totals'] == [{'currency': 'CNY', 'amount': None}]
    coverage = stats['import_coverage']
    assert (coverage['unresolved_records'], coverage['cancelled_records'], coverage['excluded_flows']) == ((7, 2, 1) if conflict else (0, 6, 3))
    package = public_package(client, cid, tmp_path)
    assert package['rawFlows'] == 5
    assert package['rawAmountTotals'] == [{'currency': 'CNY', 'amount': '40.00'}]
    assert sorted((row['trade_no'], row['amount'], row['balance'], row['self_account']) for row in package['tables']['flows']) == [
        ('SYN-P', '10.00', '90.00', 'SYN-A'), ('SYN-Q', '20.00', '80.00', 'SYN-A'),
        ('SYN-W', '1.00', '100.00', 'SYN-A'), ('SYN-XP', '7.00', '193.00', 'SYN-X'), ('SYN-XW', '2.00', '200.00', 'SYN-X')]


def test_public_suffixless_package_retains_source_and_replay_adds_zero(client, make_case, fixture_dir, tmp_path):
    cid = make_case('SYN Windows public suffixless')
    upload(client, cid, fixture_dir, 'ordinary')
    package = public_package(client, cid, tmp_path)
    source = package['tables']['import_sources'][0]
    assert source['filename'] == 'ordinary' and source['size_bytes'] == (fixture_dir / 'ordinary').stat().st_size
    assert source['sha256'] == hashlib.sha256((fixture_dir / 'ordinary').read_bytes()).hexdigest()
    assert source['parser_version'] == 'funds-trace-table-v17'
    assert package['tables']['import_records'][0]['member_path'] == 'table.csv'
    assert package['rawAmountTotals'] == [{'currency': 'USD', 'amount': '2.03'}]
    restored = client.post('/api/cases/import', files={'file': ('synthetic.fundcase', (tmp_path / 'synthetic.fundcase').read_bytes())})
    assert restored.status_code == 200
    _, repeated = upload(client, str(restored.json()['case_id']), fixture_dir, 'ordinary')
    assert repeated['result']['ok'] == 0


@pytest.mark.parametrize('defect', ['duplicate', 'missing', 'extra', 'version', 'live'])
def test_inspector_rejects_non_fixed_public_packages(tmp_path, defect):
    file = tmp_path / 'synthetic.fundcase'
    if defect == 'live':
        file.write_bytes(b'not a public zip package')
        with pytest.raises(zipfile.BadZipFile):
            fixtures.inspect_package(file)
        return
    with zipfile.ZipFile(file, 'w') as archive:
        archive.writestr('meta.json', json.dumps({'format': 3 if defect == 'version' else 4, 'source_files': 'not_included'}))
        for table in fixtures.TABLES:
            if defect != 'missing' or table != 'flows':
                archive.writestr(table + '.parquet', b'not read if structural validation fails')
        if defect == 'extra':
            archive.writestr('../fund.duckdb', b'not extracted')
        if defect == 'duplicate':
            with pytest.warns(UserWarning, match='Duplicate'):
                archive.writestr('flows.parquet', b'duplicate')
    with pytest.raises(ValueError):
        fixtures.inspect_package(file)
