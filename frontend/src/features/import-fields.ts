export const fieldNames: Record<string, string> = {
  trade_no: '流水号', trade_time: '交易时间', time_part: '交易时刻', _time2: '时间补充', amount: '交易金额', balance: '交易后余额', currency: '币种',
  payer_name: '付款户名', payer_account: '付款账号', payer_bank: '付款开户行', payee_name: '收款户名', payee_account: '收款账号', payee_bank: '收款开户行',
  self_account: '本方账号', self_name: '本方户名', counter_account: '对手账号', counter_name: '对手户名', counter_bank: '对手开户行',
  self_card: '本方卡号', counter_card: '对手卡号', feedback_reason: '反馈原因',
  direction: '收支方向', income_amt: '收入金额', expense_amt: '支出金额', summary: '摘要', remark: '备注', voucher_type: '凭证种类', channel: '渠道', status: '交易状态', voucher_reference: '凭证引用',
};
export const fieldsA = ['trade_time', '_time2', 'payer_account', 'payer_name', 'payer_bank', 'payee_account', 'payee_name', 'payee_bank', 'amount', 'trade_no', 'balance', 'currency', 'self_account', 'self_name', 'summary', 'channel'];
export const fieldsB = ['trade_time', '_time2', 'self_account', 'self_name', 'counter_account', 'counter_name', 'counter_bank', 'direction', 'amount', 'income_amt', 'expense_amt', 'trade_no', 'balance', 'currency', 'summary', 'channel'];
export const modeFields = {
  A: new Set([...fieldsA, 'payer_bank', 'payee_bank', 'self_name', 'channel', '_time2']),
  B: new Set([...fieldsB, 'counter_bank', 'channel', '_time2']),
};
export function importFieldRequirement(field: string, mode: 'A' | 'B', mapping: Record<string, string>) {
  const amounts = mapping.income_amt && mapping.expense_amt;
  if (field === 'trade_time' || (field === 'self_account' && mode === 'B')) return { required: true, note: '必填' };
  const pair = ({ payer_name: 'payer_account', payer_account: 'payer_name',
    payee_name: 'payee_account', payee_account: 'payee_name', counter_name: 'counter_account', counter_account: 'counter_name' } as Record<string, string>)[field];
  if (pair) return { required: !mapping[pair], note: `与${fieldNames[pair]}二选一` };
  if (field === 'amount') return { required: !amounts, note: mode === 'A' ? '必填' : '可用收入与支出列替代' };
  if (field === 'direction') return { required: !amounts, note: '可用收入与支出列替代' };
  if (field === 'income_amt' || field === 'expense_amt') return { required: !mapping.amount, note: '收入与支出列共同替代交易金额' };
  return { required: false, note: '选填' };
}
