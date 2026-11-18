import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { ImportStepper } from './ImportStepper';
import type { ImportItem } from './import-workflow.mjs';

const item=(phase:ImportItem['phase'], result?:NonNullable<ImportItem['job']>['result']):ImportItem=>({id:'1',filename:'synthetic.csv',directory:false,phase,importId:'0123456789ab',preview:null,resume:null,job:result?{case_id:'1',import_id:'0123456789ab',stage:'done',done:1,total:1,result,error:null,message:'完成'}:null,mode:'B',mapping:{},marked:[],selfAccounts:[],selfAccountsText:'',referencePolicy:false,dirty:false,error:''});
const result={total:1,ok:1,skipped:0,rows_dup:0,batch_id:'1',bad_examples:[],self_accounts:[],self_accounts_count:0,main_matched:[],accounts_in_batch:0,main_detected:[]};
const tableSummary={profile:'synthetic',record_total:2,accepted:1,unresolved:0,non_monetary:1,non_transaction:0,auxiliary:0,reason_counts:{},currency_totals:{},members:[]};

it('places a single real progress control in the second connector and keeps all three nodes read-only',()=>{
  render(<ImportStepper items={[item('done',result),{...item('polling'),id:'2'}]} processing total={3} confirmed={2} />);
  expect(screen.getAllByRole('listitem')).toHaveLength(3);expect(screen.queryAllByRole('button')).toHaveLength(0);
  expect(screen.getAllByRole('progressbar')).toHaveLength(1);
  expect(screen.getByRole('progressbar',{name:'导入处理进度'}).getAttribute('value')).toBe(String(2/3));
  expect(document.querySelector('[aria-current="step"]')?.textContent).toContain('识别与导入');
  expect(document.querySelector('.ft-step-spinner')).toBeTruthy();
});
it('uses activity for unknown totals and pauses during a real mapping confirmation',()=>{
  const view=render(<ImportStepper items={[item('previewing')]} processing confirmed={0} />);
  expect(screen.getByRole('progressbar').hasAttribute('value')).toBe(false);expect(document.querySelector('.ft-progress.is-active.is-indeterminate')).toBeTruthy();
  view.rerender(<ImportStepper items={[item('polling')]} processing total={1} confirmed={0} />);
  expect(screen.getByRole('progressbar').getAttribute('value')).toBe('0');expect(document.querySelector('.ft-progress.is-indeterminate')).toBeNull();
  view.rerender(<ImportStepper items={[item('ready')]} processing={false} total={1} confirmed={0} />);
  expect(screen.getByText('等待确认')).toBeTruthy();expect(document.querySelector('.ft-step-spinner')).toBeNull();expect(document.querySelector('.ft-progress.is-active')).toBeNull();
});
it('separates normal exclusions from records needing review and displays a solid terminal glyph',()=>{
  const view=render(<ImportStepper items={[item('done',{...result,skipped:1,table_summary:tableSummary})]} processing={false} total={1} confirmed={1} />);
  expect(screen.getByText('导入完成')).toBeTruthy();expect(document.querySelector<HTMLOListElement>('ol')?.dataset.outcome).toBe('complete');
  expect(document.querySelector<HTMLLIElement>('li:last-child')?.dataset.state).toBe('complete');expect(document.querySelector('.ft-step-spinner')).toBeNull();
  view.rerender(<ImportStepper items={[item('done',{...result,skipped:1,table_summary:{...tableSummary,unresolved:1,non_monetary:0}})]} processing={false} total={1} confirmed={1} />);
  expect(screen.getByText('完成，待核对')).toBeTruthy();expect(document.querySelector('li:last-child .ft-icon')?.getAttribute('style')).toContain('import-circle-alert.svg');
  expect(screen.getByRole('progressbar').getAttribute('value')).toBe('1');
});
it('retains confirmed visual progress without exposing a false numeric result during uncertainty or requery',()=>{
  const view=render(<ImportStepper items={[item('done',result),{...item('unknown'),id:'2'}]} processing={false} total={2} confirmed={1} />);
  expect(screen.getByText('结果待核对')).toBeTruthy();expect(screen.getByRole('progressbar').hasAttribute('value')).toBe(false);
  expect(document.querySelector('.ft-progress-fill')?.getAttribute('style')).toContain('scaleX(0.5)');expect(document.querySelector('.ft-progress.is-active')).toBeNull();
  view.rerender(<ImportStepper items={[item('done',result),{...item('polling'),id:'2',queryingOnly:true}]} processing total={2} confirmed={1} />);
  expect(document.querySelector<HTMLOListElement>('ol')?.dataset.outcome).toBe('unknown');expect(document.querySelector('.ft-step-spinner')).toBeNull();
});
it('distinguishes confirmed failure and stop and never replays completion motion for a historical receipt',()=>{
  const view=render(<ImportStepper items={[item('failed')]} processing={false} total={1} confirmed={1} />);
  expect(screen.getByText('导入失败')).toBeTruthy();expect(document.querySelector<HTMLOListElement>('ol')?.dataset.outcome).toBe('error');expect(document.querySelector('.ft-progress.is-complete')).toBeNull();
  view.rerender(<ImportStepper items={[item('skipped')]} processing={false} total={1} confirmed={1} />);expect(screen.getByText('已停止')).toBeTruthy();
  view.rerender(<ImportStepper items={[item('done',result)]} processing={false} total={1} confirmed={1} animateCompletion={false} />);
  expect(screen.getByText('导入完成')).toBeTruthy();expect(document.querySelector('[data-animate-completion="true"]')).toBeNull();expect(document.querySelector('.ft-progress.is-complete')).toBeNull();
});
