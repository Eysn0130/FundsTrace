import { describe, expect, it } from 'vitest';
import { captureDroppedFiles } from './directory-files';

function leaf(name: string): FileSystemEntry {
  return { name, isFile: true, isDirectory: false, file(resolve: (file: File) => void) { resolve(new File(['synthetic'], name)); } } as FileSystemFileEntry;
}
function directory(name: string, pages: FileSystemEntry[][]): FileSystemEntry {
  return { name, isFile: false, isDirectory: true, createReader() {
    let index = 0; return { readEntries(resolve: (entries: FileSystemEntry[]) => void) { resolve(pages[index++] ?? []); } };
  } } as FileSystemDirectoryEntry;
}
function transfer(entry: FileSystemEntry): DataTransfer {
  return { items: [{ kind: 'file', webkitGetAsEntry: () => entry, getAsFile: () => null }], files: [] } as unknown as DataTransfer;
}
describe('dropped directory enumeration', () => {
  it('reads every reader page and preserves nested paths and file contents', async () => {
    const drop = captureDroppedFiles(transfer(directory('资料', [[leaf('流水.csv')], [directory('银行', [[leaf('账户.csv')]])]])));
    const files = await drop.read(new AbortController().signal);
    expect(drop.directory).toBe(true);
    expect(files.map(file => file.name)).toEqual(['资料/流水.csv', '资料/银行/账户.csv']);
    expect(files.map(file => file.size)).toEqual([9, 9]);
  });
  it('does not silently skip an unreadable member or return a partial selection', async () => {
    const drop = captureDroppedFiles({ items: [{ kind: 'file', getAsFile: () => new File(['ok'], 'ok.csv') },
      { kind: 'file', getAsFile: () => null }], files: [] } as unknown as DataTransfer);
    await expect(drop.read(new AbortController().signal)).rejects.toThrow('部分目录项目无法读取');
  });
  it('rejects a revoked selection and an empty directory before upload', async () => {
    const drop = captureDroppedFiles(transfer(directory('空目录', [[]])));
    await expect(drop.read(new AbortController().signal)).rejects.toThrow('没有可读取的文件');
    const controller = new AbortController(); controller.abort();
    await expect(drop.read(controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
  });
});
