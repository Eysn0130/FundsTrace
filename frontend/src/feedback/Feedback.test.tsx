import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { FeedbackBoundary, FeedbackProvider, FeedbackViewport, createFeedbackChannel, useFeedback } from './Feedback';
function Example() { const feedback = useFeedback(); return <button onClick={() => feedback.task({ id: 'test', operation: 'analysis', status: 'unknown', title: '合成任务待核对' })}>开始合成任务</button>; }
describe('React shared feedback boundary', () => {
  it('renders local fallback and revokes old callbacks on scope abort', async () => {
    const controller = new AbortController(), channel = createFeedbackChannel({ scope: { caseId: '1', generation: 1 }, signal: controller.signal });
    const view = render(<FeedbackProvider port={channel.port}><Example /><FeedbackViewport store={channel.store} /></FeedbackProvider>);
    fireEvent.click(screen.getByRole('button', { name: '开始合成任务' })); expect(await screen.findByText('合成任务待核对')).toBeTruthy();
    act(() => controller.abort());
    expect(screen.queryByText('合成任务待核对')).toBeNull();
    channel.port.task({ id: 'stale', status: 'error', title: '旧案件回调' });
    expect(channel.store.snapshot().tasks).toHaveLength(0); expect(screen.queryByText('旧案件回调')).toBeNull();
    view.unmount();
  });
  it('uses an optional host port without a second local dock', () => {
    const port = { notify: vi.fn(), task: vi.fn() };
    render(<FeedbackBoundary port={port} scope={{ caseId: '1', generation: 1 }} signal={new AbortController().signal}><Example /></FeedbackBoundary>);
    fireEvent.click(screen.getByRole('button', { name: '开始合成任务' }));
    expect(port.task).toHaveBeenCalledWith(expect.objectContaining({ status: 'unknown' })); expect(screen.queryByRole('region', { name: '持续任务' })).toBeNull();
  });
  it('creates a local dock for an independent embedded host preview', async () => {
    render(<FeedbackBoundary scope={{ caseId: 'host-preview', generation: 1 }} signal={new AbortController().signal}><Example /></FeedbackBoundary>);
    fireEvent.click(screen.getByRole('button', { name: '开始合成任务' }));
    await waitFor(() => expect(screen.getByText('合成任务待核对')).toBeTruthy());
    expect(document.querySelector('.funds-feedback.ff-root .ff-dock')).toBeTruthy();
  });
});
