"""CI-only literal import fixtures and public format-4 package inspection.

No application imports or live database access. xlwt is a development dependency.
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal
import hashlib
from importlib.metadata import version
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import zipfile

ROOT = Path(__file__).absolute().parents[1]
STAMP = (2026, 1, 1, 0, 0, 0)
HEADER = ['交易流水号', '交易时间', '本方账号', '交易对手账卡号', '收付标志', '交易金额', '币种代码']
FORMAT_ROWS = [
    ['SYN-F1', '2026-01-01 10:00:00', '000SYN-A', '000SYN-B', '出', '123.45', 'CNY'],
    ['SYN-F2', '2026-01-01 11:00:00', '000SYN-A', '000SYN-B', '进', '2.03', 'CNY'],
    ['SYN-F3', '2026-01-01 12:00:00', '000SYN-A', '000SYN-C', '出', '4.56', 'USD'],
]
CHAIN_COLUMNS = '交易卡号 交易账号 查询反馈结果原因 交易户名 交易证件号码 交易时间 交易金额 交易余额 收付标志 交易对手账卡号 现金标志 对手户名 对手身份证号 对手开户银行 摘要说明 交易币种 交易网点名称 交易发生地 交易是否成功 传票号 IP地址 MAC地址 对手交易余额 交易流水号 日志号 凭证种类 凭证号 交易柜员号 备注 商户名称 商户代码 交易类型'.split()
TABLES = ('flows', 'batches', 'tracked_flows', 'canvases', 'main_accounts', 'account_ids',
          'account_photos', 'focus_nodes', 'import_sources', 'flow_sources', 'import_members', 'import_records')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def csv_bytes(rows):
    text = io.StringIO(newline='')
    csv.writer(text).writerows(rows)
    return text.getvalue().encode('utf-8-sig')


def zip_bytes(members):
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in members:
            info = zipfile.ZipInfo(name, STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)
    return target.getvalue()


def workbook_bytes(rows, suffix):
    if suffix == '.xlsx':
        from datetime import datetime
        from openpyxl import Workbook
        book = Workbook()
        book.properties.created = book.properties.modified = datetime(2026, 1, 1)
        book.active.title = 'Synthetic transactions'
        for row in rows:
            book.active.append(row)
        stream = io.BytesIO()
        book.save(stream)
        book.close()
        # Freeze container metadata as well as literal cell values.
        with zipfile.ZipFile(io.BytesIO(stream.getvalue())) as archive:
            members = []
            for name in archive.namelist():
                data = archive.read(name)
                if name == 'docProps/core.xml':
                    data = re.sub(rb'(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)',
                                  lambda match: match[1] + b'2026-01-01T00:00:00Z' + match[2], data)
                members.append((name, data))
            return zip_bytes(members)
    import xlwt
    book = xlwt.Workbook()
    sheet = book.add_sheet('Synthetic transactions')
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            sheet.write(r, c, value)
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


def chain_rows(conflicting=False):
    # Root-owned literal cancellation oracle: not derived from parser output.
    specs = [('W', '10:00:00', '1.00', '100.00', 'SYN-A'),
             ('P', '10:01:00', '10.00', '90.00', 'SYN-A'),
             ('P', '10:02:00', '-10.00', '100.00', 'SYN-A'),
             ('Q', '10:03:00', '20.00', '80.00', 'SYN-A'),
             ('Q', '10:04:00', '-20.00', '100.00', 'SYN-A'),
             ('XW', '11:00:00', '2.00', '200.00', 'SYN-X'),
             ('XP', '11:01:00', '7.00', '193.00', 'SYN-X'),
             ('XP', '11:02:00', '-7.00', '200.00', 'SYN-X')]
    result = []
    for ref, clock, money, balance, owner in specs:
        row = dict.fromkeys(CHAIN_COLUMNS, '')
        row.update({'交易账号': owner, '交易户名': 'Synthetic owner', '交易证件号码': 'SYN-ID',
                    '交易时间': '2026-01-01 ' + clock, '交易金额': money, '交易余额': balance,
                    '收付标志': '出', '交易币种': 'CNY', '交易是否成功': '01',
                    '交易流水号': 'SYN-' + ref, '凭证号': 'SYN-' + ref,
                    '交易对手账卡号': 'SYN-OTHER', '对手身份证号': 'SYN-OTHER-ID',
                    '商户代码': 'SYN-MERCHANT', '摘要说明': '冲正' if money.startswith('-') else 'Synthetic summary',
                    '商户名称': '冲正' if money.startswith('-') else 'Synthetic summary', '对手交易余额': money})
        result.append(row)
    if conflicting:
        result[0]['商户名称'] = 'Synthetic prior witness explanation'
    return [CHAIN_COLUMNS] + [[row[col] for col in CHAIN_COLUMNS] for row in result]


def generate(output):
    versions = {name: version(name) for name in ('openpyxl', 'xlwt', 'duckdb')}
    if versions != {'openpyxl': '3.1.5', 'xlwt': '1.3.0', 'duckdb': '1.5.6'}:
        raise ValueError('Fixture interpreter does not match locked development packages')
    output = output.absolute()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('Fixture output must be empty and operation-owned')
    simple = csv_bytes([HEADER, *FORMAT_ROWS])
    region = [
        ['date', 'payer_account', 'payee_account', 'amount', 'extra1', 'extra2'],
        ['2026-01-01', 'SYN-A', 'SYN-B', '1.23', '', ''],
        ['2026-01-02', 'payee_account', 'payer_account', '100', 'date', 'amount'],
        ['2026-01-04', 'SYN-D', 'SYN-C', '99.00', '2026-01-03', '2.34'],
        ['date', 'payee_account', 'payer_account', 'extra', 'amount', '币种代码'],
        ['2026-01-05', 'SYN-RECIPIENT', 'SYN-SENDER', 'uninterpreted', '3.45', 'USD'],
    ]
    import xlwt
    typed = [['date', 'payer_account', 'payee_account', 'amount', 'currency'],
             ['2026-04-01', '00011112222333344445', 'SYN-B', '1.23', 'CNY'],
             ['2026-04-02', 'SYN-A', 'SYN-C', xlwt.Formula('1+2'), 'CNY'],
             ['2026-04-03', 'SYN-A', 'SYN-D', True, 'CNY']]
    files = {'formats.csv': simple, 'formats.xlsx': workbook_bytes([HEADER, *FORMAT_ROWS], '.xlsx'),
             'formats.xls': workbook_bytes([HEADER, *FORMAT_ROWS], '.xls'),
             'formats.zip': zip_bytes([('bank/container.zip', zip_bytes([('nested/renamed-transactions.csv', simple)]))]),
             'regions.csv': csv_bytes(region), 'regions.xlsx': workbook_bytes(region, '.xlsx'),
             'typed-controls.xls': workbook_bytes(typed, '.xls'),
             'ordinary': csv_bytes([['date', 'payer_account', 'payee_account', 'amount', 'currency'],
                                    ['2026-02-01', 'SYN-P01-A', 'SYN-P01-B', '2.03', 'USD']])}
    for label, conflicting in [('chain', False), ('chain-conflict', True)]:
        value = csv_bytes(chain_rows(conflicting))
        files[label + '.csv'] = value
        files[label + '.zip'] = zip_bytes([('bank/renamed.csv', value)])
    for name, data in files.items():
        (output / name).write_bytes(data)
    receipt = {'format': 1, 'kind': 'fundstrace-windows-import-fixtures', 'outputRoot': str(output),
               'python': sys.executable, 'versions': versions,
               'developmentLockSha256': digest((ROOT / 'requirements-dev.lock').read_bytes()),
               'generatorSha256': digest(Path(__file__).read_bytes()),
               'files': [{'name': name, 'bytes': len(data), 'sha256': digest(data)} for name, data in sorted(files.items())]}
    (output / 'fixture-receipt.json').write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + '\n', encoding='utf8')
    return receipt


def inspect_package(package):
    """Read fixed public tables from an exported package, never a live database."""
    import duckdb
    package = package.absolute()
    if not package.is_file() or package.is_symlink():
        raise ValueError('Expected a regular public package')
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        expected = {'meta.json', *(table + '.parquet' for table in TABLES)}
        if len(names) != len(set(names)) or set(names) != expected:
            raise ValueError('Unexpected or missing synthetic package members')
        if any(entry.file_size > 32 * 1024 * 1024 for entry in archive.infolist()):
            raise ValueError('Oversized synthetic package')
        meta = json.loads(archive.read('meta.json'))
        if meta.get('format') != 4 or meta.get('source_files') != 'not_included':
            raise ValueError('Expected public format-4 export')
        tables = {}
        # Only fixed literal basenames are written; no archive extraction/path use.
        with tempfile.TemporaryDirectory(prefix='fundstrace-package-facts-') as temporary:
            with duckdb.connect(':memory:') as connection:
                for table in TABLES:
                    file = Path(temporary) / (table + '.parquet')
                    file.write_bytes(archive.read(table + '.parquet'))
                    cursor = connection.execute('select * from read_parquet(?)', [str(file)])
                    columns = [field[0] for field in cursor.description]
                    rows = []
                    for raw in cursor.fetchall():
                        row = dict(zip(columns, raw))
                        for key, value in row.items():
                            if value is not None and (isinstance(value, Decimal) or key in {'id', 'batch_id', 'flow_id', 'canvas_id'}):
                                row[key] = str(value)
                            elif hasattr(value, 'isoformat'):
                                row[key] = value.isoformat()
                        rows.append(row)
                    tables[table] = rows
    totals = {}
    for row in tables['flows']:
        money = Decimal(row['amount'])
        if not money.is_finite() or money < 0:
            raise ValueError('Invalid raw public monetary fact')
        totals[row['currency']] = totals.get(row['currency'], Decimal(0)) + money
    return {'format': 1, 'packageSha256': digest(package.read_bytes()), 'meta': meta, 'tables': tables,
            'rawFlows': len(tables['flows']),
            'rawAmountTotals': [{'currency': code, 'amount': format(total, '.2f')} for code, total in sorted(totals.items())]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--output-dir', type=Path)
    mode.add_argument('--inspect-package', type=Path)
    parser.add_argument('--output-json', type=Path)
    args = parser.parse_args()
    if args.output_dir:
        if args.output_json:
            parser.error('--output-json is only for package inspection')
        result = generate(args.output_dir)
        print(json.dumps({'generated': len(result['files']), 'receipt': 'fixture-receipt.json'}))
    else:
        if not args.output_json or not args.output_json.is_absolute():
            parser.error('Package inspection requires absolute --output-json')
        args.output_json.write_text(json.dumps(inspect_package(args.inspect_package), ensure_ascii=False, indent=2) + '\n', encoding='utf8')
        print(json.dumps({'inspected': True}))


if __name__ == '__main__':
    main()
