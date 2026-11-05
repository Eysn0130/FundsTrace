/** Capture entries synchronously: a drop's DataTransfer is protected after dispatch. */
export function captureDroppedFiles(transfer: DataTransfer): { directory: boolean; read: (signal: AbortSignal) => Promise<File[]> } {
  const items = Array.from(transfer.items ?? []).filter(item => item.kind === 'file');
  const entries = items.map(item => ({ entry: item.webkitGetAsEntry?.(), file: item.getAsFile() }));
  const fallback = Array.from(transfer.files ?? []);
  const directory = entries.some(item => item.entry?.isDirectory) || Math.max(entries.length, fallback.length) > 1;
  return { directory, async read(signal) {
    const files: File[] = [];
    const check = () => { if (signal.aborted) throw new DOMException('读取目录已中止。', 'AbortError'); };
    async function visit(entry: FileSystemEntry, parent = ''): Promise<void> {
      check();
      const path = parent ? `${parent}/${entry.name}` : entry.name;
      if (entry.isFile) {
        const file = await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject));
        check();
        // File.name carries the relative path for dropped entries; no source bytes are rewritten.
        files.push(new File([file], path, { type: file.type, lastModified: file.lastModified }));
      } else if (entry.isDirectory) {
        const reader = (entry as FileSystemDirectoryEntry).createReader();
        for (;;) {
          check();
          const children = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
          if (!children.length) break;
          for (const child of children) await visit(child, path);
        }
      } else throw new Error('目录包含无法读取的项目，请通过选择文件夹重新导入。');
    }
    check();
    if (entries.length) {
      for (const item of entries) {
        if (item.entry) await visit(item.entry);
        else if (item.file) files.push(item.file);
        else throw new Error('部分目录项目无法读取，尚未提交导入。请通过选择文件夹重试。');
      }
    } else files.push(...fallback);
    check();
    if (!files.length) throw new Error('所选目录没有可读取的文件，尚未提交导入。');
    return files;
  } };
}
