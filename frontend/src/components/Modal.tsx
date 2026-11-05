import { useEffect, useId, useRef, type ReactNode } from 'react';

function availableFocus(element: HTMLElement) {
  if (!element.isConnected || element.matches(':disabled,[aria-disabled="true"]')) return false;
  for (let parent: HTMLElement | null = element; parent; parent = parent.parentElement) {
    if (parent.hidden || parent.inert || parent.classList.contains('page-inactive') || parent.getAttribute('aria-hidden') === 'true') return false;
    const style = getComputedStyle(parent);
    if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false;
    if (parent instanceof HTMLDetailsElement && !parent.open && !parent.querySelector('summary')?.contains(element)) return false;
  }
  return true;
}

export function Modal({ title, children, onClose, busy = false }: { title: string; children: ReactNode; onClose: () => void; busy?: boolean }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const scope = previous?.closest('.funds-trace') || element?.closest('.funds-trace');
    element?.showModal();
    window.dispatchEvent(new CustomEvent('funds:feedback-modal', { detail: { open: !!document.querySelector('dialog[open]') } }));
    // Start at the title of long verification/import dialogs, keeping the
    // native dialog focus trap and Escape behavior inside this document.
    const initial = element?.querySelector<HTMLElement>('[autofocus]') || element;
    initial?.focus({ preventScroll: true });
    return () => {
      element?.close();
      window.dispatchEvent(new CustomEvent('funds:feedback-modal', { detail: { open: !!document.querySelector('dialog[open]') } }));
      const candidates = (root: Element) => [...root.querySelectorAll<HTMLElement>('button,input,select,textarea,a[href],summary,[tabindex]')]
        .find(node => !element?.contains(node) && (node.tabIndex >= 0 || node.matches('summary')) && availableFocus(node));
      const target = previous && availableFocus(previous) ? previous : (scope && candidates(scope)) || candidates(document.body);
      target?.focus({ preventScroll: true });
    };
  }, []);
  return <dialog ref={dialog} className="modal" aria-labelledby={titleId} tabIndex={-1} onCancel={event => {
    if (event.target !== event.currentTarget) return;
    event.preventDefault(); if (!busy) onClose();
  }}>
    <header className="modal-heading"><h2 id={titleId}>{title}</h2><button type="button" className="icon-button" disabled={busy} aria-label="关闭对话框" onClick={onClose}>×</button></header>
    {children}
  </dialog>;
}
