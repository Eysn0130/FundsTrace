import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { FeedbackBoundary, FeedbackProvider, FeedbackViewport, createFeedbackChannel, useFeedback } from './Feedback';

function Example() { const feedback = useFeedback(); return <button onClick={() => { feedback.task({ id: 'test', operation: 'analysis', status: 'unknown', title: '合成任务待核对' }); feedback.notify({ severity: 'info', title: '合成短提示' }); }}>开始合成操作</button>; }
const localChannel = () => createFeedbackChannel({ scope: { caseId: '1', generation: 1 }, signal: new AbortController().signal });

describe('Toast-only shared feedback', () => {
  it('renders short feedback and revokes callbacks and messages on scope abort', () => {
    const controller = new AbortController(), channel = createFeedbackChannel({ scope: { caseId: '1', generation: 1 }, signal: controller.signal });
    const view = render(<FeedbackProvider port={channel.port}><Example /><FeedbackViewport store={channel.store} /></FeedbackProvider>);
    fireEvent.click(screen.getByRole('button', { name: '开始合成操作' }));
    expect(screen.getByText('合成短提示')).toBeTruthy(); expect(screen.queryByText('合成任务待核对')).toBeNull();
    act(() => controller.abort()); expect(screen.queryByText('合成短提示')).toBeNull();
    channel.port.notify({ severity: 'error', title: '旧案件回调' });
    expect(channel.store.snapshot().tasks).toHaveLength(0); expect(screen.queryByText('旧案件回调')).toBeNull(); view.unmount();
  });
  it('uses a host port without rendering any local feedback controls', () => {
    const port = { notify: vi.fn(), task: vi.fn() };
    render(<FeedbackBoundary port={port} scope={{ caseId: '1', generation: 1 }} signal={new AbortController().signal}><Example /></FeedbackBoundary>);
    fireEvent.click(screen.getByRole('button', { name: '开始合成操作' }));
    expect(port.task).toHaveBeenCalledWith(expect.objectContaining({ status: 'unknown' }));
    expect(port.notify).toHaveBeenCalledWith(expect.objectContaining({ title: '合成短提示' })); expect(document.querySelector('.funds-feedback')).toBeNull();
  });
  it('adds no task entry, panel, bottom bar or footer and does not cancel tasks on renderer removal', () => {
    const channel = localChannel(); channel.port.task({ id: 'original', status: 'running', title: '合成原任务' });
    const view = render(<FeedbackViewport store={channel.store} />);
    expect(document.querySelector('.ff-entry,.ff-panel,.ff-dock,.funds-feedback footer')).toBeNull();
    expect(screen.queryByRole('button')).toBeNull(); expect(screen.queryByText('合成原任务')).toBeNull();
    view.unmount(); expect(channel.store.snapshot().tasks[0]).toMatchObject({ id: 'original', status: 'running', dismissed: false }); channel.dispose();
  });
  it('pauses a short message on hover and resumes its remaining time after leaving', () => {
    vi.useFakeTimers(); const channel = localChannel(); channel.port.notify({ id: 'short', severity: 'info', title: '合成短消息', duration: 4000 });
    const view = render(<FeedbackViewport store={channel.store} />);
    try {
      fireEvent.mouseEnter(document.querySelector('.ff-toast-stack')!); act(() => vi.advanceTimersByTime(9000)); expect(screen.getByText('合成短消息')).toBeTruthy();
      fireEvent.mouseLeave(document.querySelector('.ff-toast-stack')!); act(() => vi.advanceTimersByTime(3999)); expect(screen.getByText('合成短消息')).toBeTruthy();
      act(() => vi.advanceTimersByTime(1)); expect(screen.queryByText('合成短消息')).toBeNull();
    } finally { view.unmount(); channel.dispose(); vi.useRealTimers(); }
  });
  it('hides messages for a modal and rejects captured actions until it closes', async () => {
    const channel = localChannel(), action = vi.fn();
    channel.port.notify({ id: 'read', severity: 'warning', title: '合成读取提示', duration: 0, actions: [{ id: 'read', label: '读取原结果', run: action }] });
    const view = render(<FeedbackViewport store={channel.store} />), button = screen.getByRole('button', { name: '读取原结果' });
    const dialog = document.createElement('dialog'); document.body.append(dialog); dialog.showModal();
    act(() => window.dispatchEvent(new CustomEvent('funds:feedback-modal', { detail: { open: true } })));
    expect(screen.queryByText('合成读取提示')?.closest('[hidden]')).toBeTruthy(); fireEvent.click(button); expect(action).not.toHaveBeenCalled(); expect(channel.store.snapshot().toasts).toHaveLength(1);
    dialog.close(); dialog.remove(); act(() => window.dispatchEvent(new CustomEvent('funds:feedback-modal', { detail: { open: false } })));
    await waitFor(() => expect(screen.getByRole('button', { name: '读取原结果' })).toBeTruthy()); fireEvent.click(button); expect(action).toHaveBeenCalledOnce();
    view.unmount(); channel.dispose();
  });
  it('rejects a captured Toast action after its case binding is revoked', () => {
    const channel = localChannel(), action = vi.fn(); channel.port.notify({ id: 'old', severity: 'warning', title: '合成旧提示', actions: [{ id: 'open', label: '旧案件动作', run: action }] });
    const view = render(<FeedbackViewport store={channel.store} />), button = screen.getByRole('button', { name: '旧案件动作' });
    act(() => channel.store.bind({ caseId: '2', generation: 2 })); fireEvent.click(button); expect(action).not.toHaveBeenCalled(); expect(screen.queryByText('合成旧提示')).toBeNull();
    view.unmount(); channel.dispose();
  });
});
