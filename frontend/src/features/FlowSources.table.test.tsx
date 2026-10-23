import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { FundsCaseClientV1 } from '../panel/host';
import { FlowSources } from './FlowSources';

describe('versioned table source positions', () => {
  it.each([['Synthetic second', 'worksheets/0002', '工作表「Synthetic second」 · 第 2 行'],
    ['', 'nested.zip/transactions.csv', '来源成员「nested.zip/transactions.csv」 · CSV 第 2 条逻辑记录（含表头和空记录）']])(
    'shows the actual original position for %s', async (sheet, memberPath, expected) => {
      const client = { sources: async () => ({ case_id: '1', flow_id: '2', total: 1, page: 1, page_size: 100, items: [{
        source_id: 'a'.repeat(32), filename: 'synthetic.xlsx', sha256: 'b'.repeat(64), size_bytes: 100,
        mode: 'A', mapping: {}, sheet: 'workbook', row_kind: 'table_record', source_row: 19,
        parser_version: 'funds-trace-table-v5', disposition: 'imported', origin: 'package',
        imported_at: '2026-01-01T00:00:00Z', source_available: false,
        member_path: memberPath, member_row: 2, record_details: { sheet },
      }] }) } as unknown as FundsCaseClientV1;
      render(<FlowSources caseId="1" flowId="2" client={client} scopeSignal={new AbortController().signal} />);
      expect(await screen.findByText(expected)).not.toBeNull();
      expect(screen.queryByText(/行号口径未标定/)).toBeNull();
    });
});
