import type { ImportItem } from './import-workflow.mjs';

export interface ImportTreeNode {
  id: string; itemId: string; name: string; path: string; kind: 'file' | 'folder' | 'archive' | 'sheet';
  children: ImportTreeNode[]; memberPaths: string[]; fileCount: number; dispositions: Record<string, number>;
}
const extension = (name: string) => name.split('.').at(-1)?.toLowerCase();
export function importIcon(name: string, kind?: ImportTreeNode['kind']) {
  const type = extension(name);
  return kind === 'folder' ? 'folder' : kind === 'archive' || type === 'zip' || type === 'rar' ? 'import-file-zip'
    : kind === 'sheet' || type === 'xlsx' || type === 'xls' ? 'import-file-type-xls' : type === 'csv' ? 'import-file-type-csv' : type === 'tsv' ? 'import-file-type-tsv' : type === 'txt' ? 'import-file-type-txt'
      : type === 'pdf' ? 'import-file-type-pdf' : 'import-file-type-generic';
}
export function buildImportTree(items: ImportItem[]): ImportTreeNode[] {
  return items.map(item => {
    const root: ImportTreeNode = { id: item.id, itemId: item.id, name: item.filename, path: '',
      kind: item.directory ? 'folder' : /\.(zip|rar)$/i.test(item.filename) ? 'archive' : 'file',
      children: [], memberPaths: [], fileCount: 0, dispositions: {} };
    const tableSummary = item.job?.result?.table_summary ?? item.preview?.table_summary;
    const summary = tableSummary ?? item.job?.result?.feedback ?? item.preview?.feedback;
    const sheetNames = new Map((tableSummary?.documents ?? []).filter(document => typeof document.sheet === 'string' && document.sheet)
      .map(document => [String(document.member_path), String(document.sheet)]));
    const lookup = new Map<string, ImportTreeNode>([['', root]]);
    for (const member of summary?.members ?? []) {
      const parts = root.kind === 'file' && ['table.csv', item.filename].includes(member.member_path) ? [] : member.member_path.split('/');
      if (root.kind === 'folder' && parts[0] === root.name) parts.shift();
      const sheetName = sheetNames.get(member.member_path);
      if (sheetName && parts.at(-2) === 'worksheets') parts.splice(-2, 1);
      let parent = root, path = '';
      root.memberPaths.push(member.member_path);
      for (let index = 0; index < parts.length; index++) {
        path = path ? `${path}/${parts[index]}` : parts[index];
        let node = lookup.get(path);
        if (!node) {
          const file = index === parts.length - 1 ? /\.[^./]+$/.test(parts[index]) : !!sheetName && index === parts.length - 2 && /\.(?:xls|xlsx)$/i.test(parts[index]);
          const sheet = index === parts.length - 1 && sheetName;
          node = { id: `${item.id}:${path}`, itemId: item.id, name: sheet || parts[index], path,
            kind: sheet ? 'sheet' : /\.(zip|rar)$/i.test(parts[index]) ? 'archive' : file ? 'file' : index === parts.length - 1 ? 'sheet' : 'folder',
            children: [], memberPaths: [], fileCount: 0, dispositions: {} };
          parent.children.push(node); lookup.set(path, node);
        }
        node.memberPaths.push(member.member_path); parent = node;
      }
    }
    const outcomes = new Map((item.job?.member_results ?? []).map(row => [row.member_path, row.dispositions]));
    function finish(node: ImportTreeNode): number {
      if (node.kind === 'file' && node.children.length === 1 && node.children[0].kind === 'sheet') node.children = [];
      const descendants = node.children.reduce((total, child) => total + finish(child), 0);
      node.fileCount = node.kind === 'file' ? 1 : node.children.length ? descendants : node.kind === 'sheet' ? 1 : 0;
      for (const path of node.memberPaths) for (const [key, count] of Object.entries(outcomes.get(path) ?? {})) node.dispositions[key] = (node.dispositions[key] ?? 0) + count;
      return node.fileCount;
    }
    finish(root);
    return root;
  });
}
export function firstImportFile(node: ImportTreeNode): ImportTreeNode {
  return node.children.length ? firstImportFile(node.children[0]) : node;
}
export function filterImportTree(nodes: ImportTreeNode[], query: string): ImportTreeNode[] {
  const term = query.trim().toLocaleLowerCase();
  if (!term) return nodes;
  return nodes.flatMap(node => {
    if (`${node.name}\n${node.path}`.toLocaleLowerCase().includes(term)) return [node];
    const children = filterImportTree(node.children, term);
    return children.length ? [{ ...node, children }] : [];
  });
}
export function flattenImportTree(nodes: ImportTreeNode[], collapsed: Set<string>, level = 1, parentId?: string, ancestorLines: boolean[] = []): { node: ImportTreeNode; level: number; parentId?: string; last: boolean; ancestorLines: boolean[] }[] {
  return nodes.flatMap((node, index) => {
    const last = index === nodes.length - 1;
    return [{ node, level, parentId, last, ancestorLines }, ...(!collapsed.has(node.id) ? flattenImportTree(node.children, collapsed, level + 1, node.id, [...ancestorLines, !last]) : [])];
  });
}
