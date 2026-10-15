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
});
