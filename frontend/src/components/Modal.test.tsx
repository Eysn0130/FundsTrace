import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Modal } from './Modal';

describe('FundsTrace modal keyboard lifecycle', () => {
  it('focuses the named dialog and restores a connected opener on unmount', () => {
    const opener = document.createElement('button');
    document.body.append(opener); opener.focus();
    const view = render(<Modal title="合成核对" onClose={() => {}}><p>合成数据</p></Modal>);
    expect(document.activeElement).toBe(screen.getByRole('dialog', { name: '合成核对' }));
    view.unmount();
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it('ignores Escape while a write is busy and allows it once ready', () => {
    const close = vi.fn();
    const view = render(<Modal title="合成导入" busy onClose={close}>合成数据</Modal>);
    const dialog = screen.getByRole('dialog', { name: '合成导入' });
    fireEvent(dialog, new Event('cancel', { cancelable: true }));
    expect(close).not.toHaveBeenCalled();
    view.rerender(<Modal title="合成导入" onClose={close}>合成数据</Modal>);
    fireEvent(dialog, new Event('cancel', { cancelable: true }));
    expect(close).toHaveBeenCalledOnce();
  });

  it.each(['disabled', 'hidden', 'inactive', 'closed-menu'] as const)('returns focus to an available control when its opener becomes %s', state => {
    const scope = document.createElement('div'); scope.className = 'funds-trace';
    const fallback = document.createElement('button'), group = document.createElement(state === 'closed-menu' ? 'details' : 'div'), opener = document.createElement('button');
    if (group instanceof HTMLDetailsElement) { group.open = true; group.append(document.createElement('summary')); }
    group.append(opener); scope.append(fallback, group); document.body.append(scope); opener.focus();
    const view = render(<Modal title="合成焦点回退" onClose={() => {}}>合成数据</Modal>, { container: scope.appendChild(document.createElement('div')) });
    if (state === 'disabled') opener.disabled = true;
    else if (state === 'hidden') group.hidden = true;
    else if (state === 'inactive') group.classList.add('page-inactive');
    else (group as HTMLDetailsElement).open = false;
    view.unmount(); expect(document.activeElement).toBe(fallback); scope.remove();
  });
});
