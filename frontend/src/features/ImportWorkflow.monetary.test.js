import { expect, test } from 'vitest';
import { createImportWorkflow } from './import-workflow.mjs';

test.each([true, false])('ordinary confirmation binds the current layout revision when present (%s)', async registered => {
  const mapping = { amount: '交易金额' };
  const revisionA = 'a'.repeat(32);
  const revisionB = 'b'.repeat(32);
  const loaded = revision => ({
    case_id: '1', import_id: 'ordinary', filename: 'ordinary.csv', mode: 'B', mapping,
    self_accounts_in_file: [], automatic: false, stats: { ok: 1 },
    table_summary: { profile: 'funds-trace-table-v69' },
    ...(registered ? { layout_review: { revision, members: [] } } : {}),
  });
  const confirmations = [];
  const flow = createImportWorkflow({
    target: { caseId: '1', name: 'Synthetic monetary role' }, signal: new AbortController().signal,
    client: {
      preview: async () => loaded(revisionA), repreview: async () => loaded(revisionB),
      commit: async (importId, body) => {
        confirmations.push(body);
        return { case_id: '1', import_id: importId, started: true };
      },
      job: async importId => ({ case_id: '1', import_id: importId, stage: 'done', result: { ok: 1 } }),
    },
  });
  try {
    await flow.start();
    await flow.upload(() => [{ name: 'ordinary.csv', size: 1 }]);
    await flow.repreview('1');
    await flow.commit('1');
    expect(confirmations).toHaveLength(1);
    expect(confirmations[0].mapping).toEqual(mapping);
    if (registered) expect(confirmations[0].ready_revision).toBe(revisionB);
    else expect(confirmations[0]).not.toHaveProperty('ready_revision');
  } finally {
    flow.dispose();
  }
});
