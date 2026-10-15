import { useEffect, useId, useRef, type ReactNode } from 'react';

export function Modal({ title, children, onClose, busy = false }: { title: string; children: ReactNode; onClose: () => void; busy?: boolean }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    element?.showModal();
    // Start at the title of long verification/import dialogs, keeping the
    // native dialog focus trap and Escape behavior inside this document.
    const initial = element?.querySelector<HTMLElement>('[autofocus]') || element;
    initial?.focus({ preventScroll: true });
    return () => {
      element?.close();
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, []);
  return <dialog ref={dialog} className="modal" aria-labelledby={titleId} tabIndex={-1} onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <header className="modal-heading"><h2 id={titleId}>{title}</h2><button type="button" className="icon-button" disabled={busy} aria-label="关闭对话框" onClick={onClose}>×</button></header>
    {children}
  </dialog>;
}
