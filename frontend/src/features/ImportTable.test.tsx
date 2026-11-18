import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { fillColumnWidths, ImportCellValue, ImportTable } from './ImportTable';
import { fitFileName } from './ImportFileName';
import { ImportFileName } from './ImportFileName';

it('fills spare width without shrinking content or overriding manually fixed columns', () => {
  expect(fillColumnWidths([100,100,80],400,new Set())).toEqual([140,140,120]);
  expect(fillColumnWidths([64,100,80],400,new Set([0]))).toEqual([64,178,158]);
  expect(fillColumnWidths([100,100,80],200,new Set())).toEqual([100,100,80]);
  expect(fillColumnWidths([64,64],400,new Set([0,1]))).toEqual([64,64]);
});

it('includes the space reserved for inline cell controls when explicitly fitting a column', () => {
  let selected:Node|undefined;
  const geometry=vi.spyOn(document,'createRange').mockImplementation(()=>({selectNodeContents:(node:Node)=>{selected=node;},getBoundingClientRect:()=>({width:(selected?.textContent||'').length*8})} as unknown as Range));
  vi.stubGlobal('getComputedStyle',()=>({paddingLeft:'10px',paddingRight:'10px',borderLeftWidth:'0px',borderRightWidth:'0px'}));
  try {
    render(<ImportTable headings={['SHA256 校验值']} widths={[220]} className="ft-report-grid" label="完整指纹"><tbody><tr><td data-fit-extra="32"><ImportCellValue>{'a'.repeat(64)}</ImportCellValue><button>复核</button></td></tr></tbody></ImportTable>);
    fireEvent.doubleClick(screen.getByRole('separator'));
    expect(screen.getByRole('separator').getAttribute('aria-valuenow')).toBe(String(64*8+20+32+2));
  } finally {geometry.mockRestore();vi.unstubAllGlobals();}
});

it('fills an automatically measured report grid and refills the remaining columns after a manual shrink', async () => {
  let selected: Node | undefined;
  const geometry = vi.spyOn(document,'createRange').mockImplementation(()=>({selectNodeContents:(node:Node)=>{selected=node;},getBoundingClientRect:()=>({width:(selected?.textContent||'').length*8})} as unknown as Range));
  const clientWidth = vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockImplementation(function(this:HTMLElement){return this.classList.contains('ft-grid-scroll')?600:0;});
  vi.stubGlobal('getComputedStyle',()=>({paddingLeft:'10px',paddingRight:'10px',borderLeftWidth:'0px',borderRightWidth:'0px'}));
  try {
    render(<ImportTable fillToSlot autoSize headings={['户名','账号','金额']} widths={[100,120,160]} moneyColumns={[2]} className="ft-report-grid" label="铺满报告"><tbody><tr><td><ImportCellValue>合成主体</ImportCellValue></td><td><ImportCellValue>000SYN-A</ImportCellValue></td><td><ImportCellValue>1,234,567.89</ImportCellValue></td></tr></tbody></ImportTable>);
    const handles=screen.getAllByRole('separator'), values=()=>handles.map(handle=>Number(handle.getAttribute('aria-valuenow')));
    await waitFor(()=>expect(values().reduce((a,b)=>a+b,0)).toBe(600));
    fireEvent.keyDown(handles[2],{key:'Home'});
    expect(values()[2]).toBe(64);expect(values().reduce((a,b)=>a+b,0)).toBe(600);
    expect(screen.getByText('1,234,567.89').textContent).toBe('1,234,567.89');
  } finally { geometry.mockRestore(); clientWidth.mockRestore(); vi.unstubAllGlobals(); }
});

it('keeps an overflowing row number hint separate from its row action',async()=>{
  vi.useFakeTimers();
  try {
    render(<ImportTable headings={['行号']} widths={[64]} label="行号操作" className="ft-preview-scroll"><tbody><tr><td><button type="button">行问题</button><ImportCellValue>1234567</ImportCellValue></td></tr></tbody></ImportTable>);
    const value=screen.getByText('1234567');Object.defineProperties(value,{scrollWidth:{value:80},clientWidth:{value:24}});
    fireEvent.pointerOver(screen.getByRole('button',{name:'行问题'}));await act(async()=>vi.advanceTimersByTime(320));
    expect(screen.queryByRole('dialog',{name:'完整内容'})).toBeNull();
    fireEvent.pointerOver(value);await act(async()=>vi.advanceTimersByTime(320));
    expect(screen.getByRole('dialog',{name:'完整内容'}).textContent).toBe('1234567');
  } finally {vi.useRealTimers();}
});

it('keeps a manually sized column when bounded source columns arrive or the view changes',()=>{
  const body=<tbody><tr><td><ImportCellValue>SYN</ImportCellValue></td><td><ImportCellValue>1.23</ImportCellValue></td></tr></tbody>;
  const view=render(<ImportTable headings={['账号','金额']} widths={[180,200]} className="ft-preview-scroll" label="动态来源">{body}</ImportTable>);
  fireEvent.keyDown(screen.getByRole('separator',{name:'调整金额列宽'}),{key:'ArrowRight'});
  view.rerender(<ImportTable headings={['账号','金额','原始·备注']} widths={[180,200,240]} className="ft-preview-scroll" label="动态来源">{body}</ImportTable>);
  expect(screen.getByRole('separator',{name:'调整金额列宽'}).getAttribute('aria-valuenow')).toBe('208');
  expect(screen.getByRole('separator',{name:'调整原始·备注列宽'}).getAttribute('aria-valuenow')).toBe('240');
  view.rerender(<ImportTable headings={['账号','金额','原始·备注']} widths={[180,200,240]} className="ft-preview-scroll" label="动态来源"><tbody><tr><td colSpan={3}>正在读取来源记录…</td></tr></tbody></ImportTable>);
  expect(screen.getByRole('separator',{name:'调整金额列宽'}).getAttribute('aria-valuenow')).toBe('208');
});

it('caps long original text during automatic sizing but fits it fully on an explicit double click',async()=>{
  let selected:Node|undefined;
  const geometry=vi.spyOn(document,'createRange').mockImplementation(()=>({selectNodeContents:(node:Node)=>{selected=node;},getBoundingClientRect:()=>({width:(selected?.textContent||'').length*10})} as unknown as Range));
  vi.stubGlobal('getComputedStyle',()=>({paddingLeft:'16px',paddingRight:'16px',borderLeftWidth:'1px',borderRightWidth:'0px',getPropertyValue:()=>''}));
  try {
    render(<ImportTable autoSize headings={['金额']} widths={[220]} moneyColumns={[0]} label="原始长值" className="ft-preview-scroll"><tbody><tr><td data-fit-text="true"><ImportCellValue>{'SYN-ORIGINAL-INVALID-AMOUNT'.repeat(30)}</ImportCellValue></td></tr></tbody></ImportTable>);
    const handle=screen.getByRole('separator',{name:'调整金额列宽'});
    await waitFor(()=>expect(handle.getAttribute('aria-valuenow')).toBe('360'));
    fireEvent.doubleClick(handle);expect(Number(handle.getAttribute('aria-valuenow'))).toBeGreaterThan(1200);
  } finally {geometry.mockRestore();vi.unstubAllGlobals();}
});

it('resizes one column without changing the other columns or cell strings', () => {
  render(<ImportTable headings={['摘要','账号','金额']} widths={[180,200,240]} moneyColumns={[2]} label="合成表格" className="ft-preview-scroll"><tbody><tr><td><ImportCellValue>长摘要完整值 SYNTHETIC-0001</ImportCellValue></td><td><ImportCellValue>000012345678901234567890</ImportCellValue></td><td className="ft-money">9,999,999,999,999,999.99</td></tr></tbody></ImportTable>);
  const handles = screen.getAllByRole('separator'), values = () => handles.map(handle => handle.getAttribute('aria-valuenow'));
  expect(values()).toEqual(['180','200','240']);
  fireEvent.keyDown(handles[1], { key: 'ArrowRight' }); expect(values()).toEqual(['180','208','240']);
  fireEvent.keyDown(handles[1], { key: 'ArrowLeft', shiftKey: true }); expect(values()).toEqual(['180','207','240']);
  expect(screen.getByText('000012345678901234567890').textContent).toBe('000012345678901234567890');
  expect(screen.getByText('9,999,999,999,999,999.99').textContent).toBe('9,999,999,999,999,999.99');
  expect(screen.getByRole('slider', { name: '3列表格水平滚动' })).toBeTruthy();
});
it.each([51, 120])('fits and remeasures the largest amount at row %i of a bounded trend page', async rowNumber => {
  let selected: Node | undefined;
  const geometry = vi.spyOn(document, 'createRange').mockImplementation(() => ({
    selectNodeContents: (node: Node) => { selected = node; },
    getBoundingClientRect: () => ({ width: (selected?.textContent || '').length * 8 }),
  } as unknown as Range));
  const clientWidth = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function(this: HTMLElement) { return this.classList.contains('ft-grid-scroll') ? 360 : 0; });
  vi.stubGlobal('getComputedStyle', () => ({ paddingLeft: '10px', paddingRight: '10px', borderLeftWidth: '0px', borderRightWidth: '0px', getPropertyValue: () => '' }));
  const headings = ['期间', '进账金额（元）', '摘要'], widths = [160, 170, 180], money = [1];
  const body = (amount: string) => <tbody>{Array.from({ length: 120 }, (_, index) => <tr key={index}>
    <td><ImportCellValue>{`期间 ${index + 1}`}</ImportCellValue></td>
    <td className="ft-money"><ImportCellValue>{index === rowNumber - 1 ? amount : '0.01'}</ImportCellValue></td>
    <td data-fit-text><ImportCellValue>{'合成期间说明'.repeat(30)}</ImportCellValue></td>
  </tr>)}</tbody>;
  const grid = (amount: string) => <ImportTable fillToSlot autoSize textMaxWidth={160} headings={headings} widths={widths} moneyColumns={money} label="完整期间金额" className="ft-report-grid">{body(amount)}</ImportTable>;
  try {
    const first = '123,456,789,012,345.67', largest = '9,999,999,999,999,999.99';
    const view = render(grid(first));
    const amountWidth = () => Number(screen.getByRole('separator', { name: '调整进账金额（元）列宽' }).getAttribute('aria-valuenow'));
    const renderedTextWidth = (value: string) => {
      const range = document.createRange(); range.selectNodeContents(screen.getByText(value));
      return range.getBoundingClientRect().width;
    };
    await waitFor(() => expect(amountWidth()).toBeGreaterThan(renderedTextWidth(first)));
    const previous = amountWidth();
    view.rerender(grid(largest));
    await waitFor(() => {
      expect(amountWidth()).toBeGreaterThan(renderedTextWidth(largest));
      expect(amountWidth()).toBeGreaterThan(previous);
    });
    expect(screen.getByRole('separator', { name: '调整摘要列宽' }).getAttribute('aria-valuenow')).toBe('160');
    expect(screen.getByText(largest).textContent).toBe(largest);
  } finally { geometry.mockRestore(); clientWidth.mockRestore(); vi.unstubAllGlobals(); }
});
it('fits the loaded content once, keeps manual widths independent and allows explicit full-content expansion', async () => {
  let selected: Node | undefined, measurements = 0;
  const geometry = vi.spyOn(document,'createRange').mockImplementation(()=>({selectNodeContents:(node:Node)=>{selected=node;},getBoundingClientRect:()=>{measurements++;return {width:(selected?.textContent||'').length*10};}} as unknown as Range));
  vi.stubGlobal('getComputedStyle',(element:Element)=>({paddingLeft:'16px',paddingRight:element.tagName==='TH'?'30px':'16px',borderLeftWidth:'1px',borderRightWidth:'0px'}));
  const headings=['行号','交易时间','金额','摘要'], widths=[96,238,220,240], money=[2], amount='1,234,567.89', summary='合成很长摘要'.repeat(35);
  const body=(time:string)=><tbody>{Array.from({length:50},(_,index)=><tr key={index}><td><ImportCellValue>{index+1}</ImportCellValue></td><td><ImportCellValue>{time}</ImportCellValue></td><td className="ft-money"><ImportCellValue>{amount}</ImportCellValue></td><td><ImportCellValue>{summary}</ImportCellValue></td></tr>)}</tbody>;
  try {
    const view=render(<ImportTable autoSize headings={headings} widths={widths} moneyColumns={money} label="自适应预览" className="ft-preview-scroll">{body('2026-11-13 09:41:26')}</ImportTable>);
    const handles=screen.getAllByRole('separator');
    await waitFor(()=>expect(handles[2].getAttribute('aria-valuenow')).toBe(String(amount.length*10+35)));
    expect(handles[3].getAttribute('aria-valuenow')).toBe('360');
    expect(measurements).toBeLessThan(70); const measured=measurements, originalTimeWidth=Number(handles[1].getAttribute('aria-valuenow'));
    fireEvent.keyDown(handles[2],{key:'Home'}); expect(handles[2].getAttribute('aria-valuenow')).toBe('64');
    fireEvent.doubleClick(handles[3]); expect(Number(handles[3].getAttribute('aria-valuenow'))).toBeGreaterThan(1200);
    expect(Number(handles[3].getAttribute('aria-valuemax'))).toBeGreaterThan(Number(handles[3].getAttribute('aria-valuenow')));
    expect(handles[3].getAttribute('aria-valuetext')).toMatch(/像素$/);
    const explicitMeasurements=measurements; fireEvent.keyDown(handles[3],{key:'ArrowRight'});
    await act(async()=>new Promise(resolve=>setTimeout(resolve,40))); expect(measurements).toBe(explicitMeasurements);
    expect(explicitMeasurements).toBeGreaterThan(measured);
    view.rerender(<ImportTable autoSize headings={headings} widths={widths} moneyColumns={money} label="自适应预览" className="ft-preview-scroll">{body('2026-11-13 09:41:26.123456')}</ImportTable>);
    await waitFor(()=>expect(Number(handles[1].getAttribute('aria-valuenow'))).toBeGreaterThan(originalTimeWidth));
    expect(handles[2].getAttribute('aria-valuenow')).toBe('64'); expect(Number(handles[3].getAttribute('aria-valuenow'))).toBeGreaterThan(1200);
    expect(screen.getAllByText(amount)).toHaveLength(50);
  } finally { geometry.mockRestore(); vi.unstubAllGlobals(); }
});
it('uses the measured filename slot and retains a distinguishing suffix and extension', () => {
  const name = '合成跨行账户流水_20261113_第三批_长文件名.xlsx', measure = (text: string) => Array.from(text).length * 10;
  expect(fitFileName(name, 600, measure)).toBe(name);
  const narrow = fitFileName(name, 240, measure), wide = fitFileName(name, 280, measure);
  expect(narrow).toContain('…'); expect(narrow).not.toContain('...'); expect(narrow.endsWith('.xlsx')).toBe(true);
  expect(measure(narrow)).toBeLessThanOrEqual(240); expect(wide.length).toBeGreaterThan(narrow.length);
  expect(fitFileName('合成.csv', 100, measure)).toBe('合成.csv');
  const compact = fitFileName('合成跨行账户流水_第三批.csv', 90, measure);
  expect(compact.startsWith('合成')).toBe(true);
  expect(compact.endsWith('.csv')).toBe(true);
  expect(measure(compact)).toBeLessThanOrEqual(90);
});
it('lets the keyboard enter, copy and dismiss an overflowing cell without reopening it on returned focus', async () => {
  vi.useFakeTimers();
  try {
    render(<ImportTable headings={['摘要']} widths={[120]} label="全值测试" className="ft-preview-scroll"><tbody><tr><td><ImportCellValue>合成完整摘要 SYNTHETIC-FULL-VALUE</ImportCellValue></td></tr></tbody></ImportTable>);
    const cell = screen.getByText('合成完整摘要 SYNTHETIC-FULL-VALUE');
    Object.defineProperties(cell, {scrollWidth:{value:300},clientWidth:{value:80}}); cell.tabIndex=0;
    const viewport=screen.getByRole('region',{name:'全值测试'});
    cell.getBoundingClientRect=()=>({left:10,right:90,top:40,bottom:60,width:80,height:20,x:10,y:40,toJSON(){return {};}});
    viewport.getBoundingClientRect=()=>({left:0,right:200,top:0,bottom:200,width:200,height:200,x:0,y:0,toJSON(){return {};}});
    act(() => cell.focus()); fireEvent.scroll(viewport); await act(async () => vi.advanceTimersByTime(320));
    expect(screen.getByRole('dialog', {name:'完整内容'})).toBeTruthy();
    fireEvent.keyDown(cell, {key:'Tab'}); expect(document.activeElement).toBe(screen.getByRole('button', {name:'复制完整值'}));
    fireEvent.keyDown(document.activeElement!, {key:'Escape'});
    expect(document.activeElement).toBe(cell); await act(async () => vi.advanceTimersByTime(500));
    expect(screen.queryByRole('dialog', {name:'完整内容'})).toBeNull();
    act(() => cell.blur()); act(() => cell.focus()); await act(async () => vi.advanceTimersByTime(320));
    const escape = new KeyboardEvent('keydown', {key:'Escape',bubbles:true,cancelable:true}); cell.dispatchEvent(escape);
    await act(async () => {}); expect(escape.defaultPrevented).toBe(true); expect(screen.queryByRole('dialog', {name:'完整内容'})).toBeNull();
  } finally { vi.useRealTimers(); }
});
it('shows the untruncated filename when its tree row receives keyboard focus', async () => {
  vi.useFakeTimers();
  const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(160);
  vi.stubGlobal('CanvasRenderingContext2D', class {});
  const context = vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({font:'',measureText:(text:string)=>({width:Array.from(text).length*10})} as unknown as CanvasRenderingContext2D);
  try {
    const name='合成账户流水_第三批_20261113_很长文件名.xlsx', path='folder/'+name;
    const click=vi.fn(), key=vi.fn(), copy=vi.fn(async()=>{}); vi.stubGlobal('navigator',{clipboard:{writeText:copy}});
    render(<div role="treeitem" tabIndex={0} onClick={click} onKeyDown={key}><ImportFileName name={name} path={path} className="ft-tree-name" /></div>);
    expect(document.querySelector('.ft-file-name')?.textContent).toContain('…');
    const row=screen.getByRole('treeitem'); act(()=>row.focus()); await act(async()=>vi.advanceTimersByTime(320));
    expect(screen.getByRole('dialog', {name:'完整内容'}).textContent).toContain(path);
    fireEvent.keyDown(row,{key:'Tab'}); const button=screen.getByRole('button',{name:'复制完整值'});
    expect(document.activeElement).toBe(button); fireEvent.keyDown(button,{key:'Enter'}); fireEvent.click(button); await act(async()=>{});
    expect(copy).toHaveBeenCalledWith(path); expect(click).not.toHaveBeenCalled(); expect(key).not.toHaveBeenCalled();
    fireEvent.keyDown(row,{key:'Escape'}); await act(async()=>vi.advanceTimersByTime(500));
    expect(screen.queryByRole('dialog', {name:'完整内容'})).toBeNull();
  } finally { width.mockRestore(); context.mockRestore(); vi.unstubAllGlobals(); vi.useRealTimers(); }
});
