import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { ImportFullValue } from './ImportFullValue';

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

function mount(copy: (value: string) => Promise<void>) {
  vi.stubGlobal('navigator', { clipboard: { writeText: copy } });
  const anchor=document.createElement('button');anchor.textContent='原始单元格';document.body.append(anchor);
  function View() {
    const [open,setOpen]=useState(true);
    return open?<ImportFullValue anchor={anchor} value="000SYN-完整原值.xlsx" onClose={()=>setOpen(false)}/>:null;
  }
  const view=render(<View/>);
  return {anchor,view,cleanup:()=>{view.unmount();anchor.remove();}};
}

it('keeps copy feedback visible, then fades, closes and restores focus without reopening the owner', async () => {
  vi.useFakeTimers();const copy=vi.fn(async()=>{}),value=mount(copy);
  try {
    const button=screen.getByRole('button',{name:'复制完整值'});act(()=>button.focus());fireEvent.click(button);
    await act(async()=>{});expect(copy).toHaveBeenCalledExactlyOnceWith('000SYN-完整原值.xlsx');
    expect(screen.getByRole('button',{name:'已复制'}).classList.contains('is-copied')).toBe(true);
    await act(async()=>vi.advanceTimersByTime(999));expect(screen.getByRole('dialog')).toBeTruthy();
    await act(async()=>vi.advanceTimersByTime(1));expect(screen.getByRole('dialog').classList.contains('is-closing')).toBe(true);
    await act(async()=>vi.advanceTimersByTime(140));expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(value.anchor);expect(value.anchor.dataset.ftValueDismissed).toBe('true');
  } finally {value.cleanup();}
});

it('keeps failed copy retryable and only schedules dismissal after a successful retry', async () => {
  vi.useFakeTimers();const copy=vi.fn().mockRejectedValueOnce(new Error('Synthetic clipboard denied')).mockResolvedValueOnce(undefined),value=mount(copy);
  try {
    fireEvent.click(screen.getByRole('button',{name:'复制完整值'}));await act(async()=>{});
    await act(async()=>vi.advanceTimersByTime(2000));expect(screen.getByRole('dialog').classList.contains('is-closing')).toBe(false);
    fireEvent.click(screen.getByRole('button',{name:'复制失败，请重试'}));await act(async()=>{});
    expect(screen.getByRole('button',{name:'已复制'})).toBeTruthy();expect(copy).toHaveBeenCalledTimes(2);
    await act(async()=>vi.advanceTimersByTime(1140));expect(screen.queryByRole('dialog')).toBeNull();
  } finally {value.cleanup();}
});

it('drops a late clipboard result when the shown value changes', async () => {
  vi.useFakeTimers();let finish!:()=>void;
  vi.stubGlobal('navigator',{clipboard:{writeText:vi.fn(()=>new Promise<void>(resolve=>{finish=resolve;}))}});
  const anchor=document.createElement('button');document.body.append(anchor);const close=vi.fn();
  const view=render(<ImportFullValue anchor={anchor} value="旧原值" onClose={close}/>);
  try {
    fireEvent.click(screen.getByRole('button',{name:'复制完整值'}));
    view.rerender(<ImportFullValue anchor={anchor} value="新原值" onClose={close}/>);
    await act(async()=>finish());await act(async()=>vi.advanceTimersByTime(2000));
    expect(screen.getByRole('dialog').textContent).toContain('新原值');expect(screen.getByRole('button',{name:'复制完整值'})).toBeTruthy();expect(close).not.toHaveBeenCalled();
    view.unmount();expect(vi.getTimerCount()).toBe(0);
  } finally {view.unmount();anchor.remove();}
});

it('cancels the scheduled copied-feedback dismissal when unmounted', async () => {
  vi.useFakeTimers();const value=mount(vi.fn(async()=>{}));
  fireEvent.click(screen.getByRole('button',{name:'复制完整值'}));await act(async()=>{});
  expect(vi.getTimerCount()).toBeGreaterThan(0);value.cleanup();expect(vi.getTimerCount()).toBe(0);
});
