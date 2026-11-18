import { render, screen, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import { ImportDataPreview } from './ImportDataPreview';

it('retains the existing thirteen normalized columns plus source position without truncating text or money', () => {
  const summary = '合成预览摘要：完整保留交易来源、核查说明和编号 SYNTHETIC-PREVIEW-000000000000000001。';
  render(<ImportDataPreview filename="synthetic-preview.xlsx" rows={[{ trade_time: '2026-10-15 08:09:07', payer_name: 'Synthetic payer', payer_account: '000012345678901234567890',
    payee_name: 'Synthetic payee', payee_account: 'SYNTHETIC-PREVIEW-ACCOUNT-B', amount: '9999999999999999.99', currency: 'USD', balance: '10000000000000000.01',
    summary, trade_no: 'SYNTHETIC-TRADE-000000000000000001', payer_bank: 'Synthetic payer bank', payee_bank: 'Synthetic payee bank', channel: 'Synthetic wire channel',
    self_account: '000098765432109876543210', self_name: 'Synthetic main account', source_row: 128 }]} />);
  const table = screen.getByRole('table');
  expect(within(table).getAllByRole('columnheader')).toHaveLength(18);
  expect(within(table).getAllByRole('cell')).toHaveLength(18);
  for (const value of ['2026-10-15 08:09:07','000012345678901234567890','SYNTHETIC-PREVIEW-ACCOUNT-B',summary,
    'SYNTHETIC-TRADE-000000000000000001','Synthetic payer bank','Synthetic payee bank','Synthetic wire channel','000098765432109876543210',
    'Synthetic main account','9,999,999,999,999,999.99','10,000,000,000,000,000.01']) expect(within(table).getByText(value)).toBeTruthy();
  expect(within(table).getByRole('cell', { name: '128' }).getAttribute('title')).toContain('原始第 128 行');
});
it('shows an own-side direction only when explicit account strings identify one side', () => {
  const base = { trade_time: '2026-01-01', trade_no: '0001', payer_name: '合成甲', payer_account: '00001', payer_bank: '',
    payee_name: '合成乙', payee_account: '00002', payee_bank: '', amount: '1234567890123456.78', balance: null, currency: 'CNY', summary: '', channel: '', self_account: '', self_name: '' };
  render(<ImportDataPreview filename="synthetic.csv" rows={[{...base,self_account:'00001'},{...base,self_account:'00002'},base,{...base,self_account:'00001',payee_account:'00001'}]} />);
  const cells = screen.getAllByRole('row').slice(1).map(row => within(row).getAllByRole('cell'));
  expect(cells.map(row => row[2].textContent)).toEqual(['转出','转入','—','—']);
  expect(cells.map(row => row[3].textContent)).toEqual(['合成乙','合成甲','—','—']);
  expect(cells[0][5].textContent).toBe('00001'); expect(cells[0][8].textContent).toBe('1,234,567,890,123,456.78');
});
