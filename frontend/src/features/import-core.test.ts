import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { expect, it } from 'vitest';

it('runs shared controller and mapping regressions in the frontend CI group too', () => {
  const root = resolve(process.cwd(), '..');
  const scripts = ['unified_import_harness.mjs', 'unified_import_host_harness.mjs', 'import_mapping_harness.mjs', 'import_queue_harness.mjs'].map(name => resolve(root, 'tests', name));
  const output = execFileSync(process.execPath, ['--test', ...scripts], { cwd: root, encoding: 'utf-8', timeout: 30000 });
  expect(output).toContain('# fail 0');
});
