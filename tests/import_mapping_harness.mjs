import assert from 'node:assert/strict';
import test from 'node:test';
import { fieldNames, fieldsA, fieldsB, modeFields, importFieldRequirement } from '../frontend/src/features/import-fields.ts';

test('the shared matrix retains every supported legacy A/B field including banks and channel', () => {
  const a = ['trade_no','trade_time','_time2','self_account','payer_name','payer_account','payer_bank','payee_name','payee_account','payee_bank','amount','balance','currency','summary','channel'];
  const b = ['trade_no','trade_time','_time2','self_name','self_account','counter_name','counter_account','counter_bank','direction','income_amt','expense_amt','amount','balance','currency','summary','channel'];
  for (const [mode, expected, fields] of [['A',a,fieldsA],['B',b,fieldsB]]) for (const key of expected) {
    assert(fields.includes(key), key); assert(modeFields[mode].has(key), key); assert.equal(typeof fieldNames[key], 'string');
  }
});
test('requirements preserve the alternatives instead of making both identity columns mandatory', () => {
  assert.equal(importFieldRequirement('payer_account','A',{payer_name:'户名'}).required,false);
  assert.equal(importFieldRequirement('payer_account','A',{}).required,true);
  assert.equal(importFieldRequirement('self_account','A',{}).required,false);
  assert.equal(importFieldRequirement('self_account','B',{}).required,true);
  assert.match(importFieldRequirement('counter_name','B',{}).note,/二选一/);
});
test('paired income/expense columns remain alternatives to amount and direction', () => {
  const mapping = {income_amt:'收入',expense_amt:'支出'};
  assert.equal(importFieldRequirement('amount','B',mapping).required,false);
  assert.equal(importFieldRequirement('direction','B',mapping).required,false);
  assert.equal(importFieldRequirement('income_amt','B',{amount:'金额'}).required,false);
  assert.equal(importFieldRequirement('trade_time','B',mapping).required,true);
});
test('describing a matrix cannot normalize source names, identifiers or amount strings', () => {
  const mapping = Object.freeze({payer_account:'<img src=x>',amount:'9007199254740991.13',self_account:'0001'});
  const before = JSON.stringify(mapping);
  for (const field of fieldsA) importFieldRequirement(field,'A',mapping);
  assert.equal(JSON.stringify(mapping),before);
});
test('time supplement, currency, balance and channel stay optional UI hints', () => {
  for (const field of ['_time2','currency','balance','channel']) assert.equal(importFieldRequirement(field,'B',{}).required,false);
});
