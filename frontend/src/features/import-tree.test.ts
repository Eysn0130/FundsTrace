import { expect, it } from 'vitest';
import { createElement } from 'react';
import { render, screen } from '@testing-library/react';
import type { ImportItem } from './import-workflow.mjs';
import { buildImportTree } from './import-tree';
import { ImportTree } from './ImportTree';

function workbook(archive = false): ImportItem {
  const prefix = archive ? 'folder.2026/book.xlsx/' : '';
  const documents = [1,2].map((n) => ({ member_path: prefix + 'worksheets/000' + n, sheet: n === 1 ? '交易明细' : '账户.资料', columns: [], header_row: 3 }));
  return { id: '1', filename: archive ? 'synthetic.zip' : 'book.xlsx', directory: false, phase: 'ready', importId: 'synthetic', job: null, resume: null,
    mode: 'B', mapping: {}, marked: [], selfAccounts: [], selfAccountsText: '', referencePolicy: false, dirty: false, error: '',
    preview: { case_id: '1', import_id: 'synthetic', filename: 'synthetic', mode: 'B', columns: [], mapping: {}, preview: [], self_accounts_in_file: [], stats: { total: 0, ok: 0, skipped: 0 },
      table_summary: { profile: 'synthetic', record_total: 0, accepted: 0, unresolved: 0, non_monetary: 0, non_transaction: 0, auxiliary: 0, currency_totals: {}, reason_counts: {},
        documents, members: documents.map(d => ({ member_path: d.member_path, role: 'table', sha256: '', size_bytes: 1, encoding: 'xlsx', row_count: 0 })) } } };
}
it('shows actual direct workbook sheet names and counts one physical file', () => {
  const [root] = buildImportTree([workbook()]);
  expect(root.fileCount).toBe(1); expect(root.children.map(x => x.name)).toEqual(['交易明细','账户.资料']);
  expect(root.children.map(x => x.kind)).toEqual(['sheet','sheet']);
  expect(root.children[1].memberPaths).toEqual(['worksheets/0002']);
});
it('keeps dotted folders as folders and sheets under the workbook inside an archive', () => {
  const [root] = buildImportTree([workbook(true)]), folder = root.children[0], book = folder.children[0];
  expect(root.fileCount).toBe(1); expect(folder.kind).toBe('folder'); expect(folder.name).toBe('folder.2026');
  expect(book.name).toBe('book.xlsx'); expect(book.children).toHaveLength(2);
  expect(book.children[0].name).toBe('交易明细'); expect(book.children[0].memberPaths).toEqual(['folder.2026/book.xlsx/worksheets/0001']);
});
it('uses real progress on its operation root and leaves unmeasured sheet progress indeterminate', () => {
  const item = workbook(); item.phase = 'polling';
  item.job = { case_id: '1', import_id: 'synthetic', stage: 'importing', done: 5, total: 10, message: '写入', result: null, error: null };
  render(createElement(ImportTree, { roots: buildImportTree([item]), items: [item], onSelect: () => {} }));
  expect(screen.getByRole('progressbar', { name: 'book.xlsx处理进度' }).getAttribute('value')).toBe('0.5');
  expect(screen.getByRole('progressbar', { name: '交易明细处理进度' }).hasAttribute('value')).toBe(false);
});
it('does not repeat the selected folder root while retaining its canonical member paths', () => {
  const item = workbook(true); item.directory = true; item.filename = 'folder.2026';
  const [root] = buildImportTree([item]);
  expect(root.name).toBe('folder.2026'); expect(root.children[0].name).toBe('book.xlsx');
  expect(root.fileCount).toBe(1); expect(root.children[0].children[0].memberPaths).toEqual(['folder.2026/book.xlsx/worksheets/0001']);
});
it('opens a one-sheet Excel at the file level without losing its original member identity', () => {
  const item = workbook(), summary = item.preview!.table_summary!;
  summary.members = summary.members.slice(0,1); summary.documents = summary.documents!.slice(0,1);
  const [root] = buildImportTree([item]);
  expect(root.name).toBe('book.xlsx'); expect(root.children).toHaveLength(0); expect(root.fileCount).toBe(1);
  expect(root.memberPaths).toEqual(['worksheets/0001']);
});
it('never labels an unconfirmed or rolled-back archive as completed', () => {
  for (const phase of ['ready','failed'] as const) {
    const item = workbook(true); item.phase = phase;
    const view = render(createElement(ImportTree, { roots: buildImportTree([item]), items: [item], onSelect: () => {} }));
    expect(screen.getAllByRole('treeitem')[0].textContent).toContain(phase === 'ready' ? '待确认' : '失败');
    expect(screen.getAllByRole('treeitem')[0].textContent).not.toContain('已完成');
    view.unmount();
  }
});
it('does not count a folder named like an Excel file as one physical workbook', () => {
  const item = workbook(true), summary = item.preview!.table_summary!;
  const documents = [1,2].map(n => ({ member_path: `evidence.xlsx/file${n}.csv`, sheet: '', columns: [] }));
  summary.documents = documents;
  summary.members = documents.map(d => ({ member_path: d.member_path, role: 'table', sha256: '', size_bytes: 1, encoding: 'utf-8', row_count: 1 }));
  const [root] = buildImportTree([item]);
  expect(root.children[0].kind).toBe('folder'); expect(root.fileCount).toBe(2); expect(root.children[0].children).toHaveLength(2);
});

it('pauses uncertain tree progress without exposing the last observed percentage as a current value', () => {
  const item=workbook();item.phase='unknown';
  item.job={case_id:'1',import_id:'synthetic',stage:'importing',done:5,total:10,message:'写入',error:null,result:null};
  const view=render(createElement(ImportTree,{roots:buildImportTree([item]),items:[item],onSelect:()=>{}}));
  const progress=screen.getByRole('progressbar',{name:'book.xlsx处理进度'});
  expect(progress.hasAttribute('value')).toBe(false);
  expect(progress.closest('.ft-progress')?.querySelector('.ft-progress-fill')?.getAttribute('style')).toContain('scaleX(0.5)');
  item.phase='polling';item.queryingOnly=true;
  view.rerender(createElement(ImportTree,{roots:buildImportTree([item]),items:[item],onSelect:()=>{}}));
  expect(screen.getByRole('progressbar',{name:'book.xlsx处理进度'}).hasAttribute('value')).toBe(false);
  expect(document.querySelector('.ft-tree-row .is-spinning')).toBeNull();
});
