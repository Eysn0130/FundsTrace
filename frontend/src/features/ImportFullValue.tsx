import { useId, useLayoutEffect, useRef, useState } from 'react';

/** One hoverable, keyboard-accessible full-value surface for names and cells. */
export function ImportFullValue({ anchor, value, onClose, onPointerEnter, onPointerLeave }: { anchor: HTMLElement; value: string; onClose: () => void; onPointerEnter?: () => void; onPointerLeave?: () => void }) {
  const panel = useRef<HTMLDivElement>(null), id = useId(), [copy, setCopy] = useState<'idle' | 'copying' | 'done' | 'failed'>('idle'), [leaving, setLeaving] = useState(false);
  const generation = useRef(0), copying = useRef(false), holdTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined), fadeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const closeRef = useRef(onClose); closeRef.current = onClose;
  useLayoutEffect(() => {
    generation.current++; copying.current = false; setCopy('idle'); setLeaving(false);
    const element = panel.current!;
    element.showPopover?.();
    const rect = anchor.getBoundingClientRect();
    element.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - element.offsetWidth - 8))}px`;
    element.style.top = `${Math.max(8, Math.min(rect.bottom + 7, window.innerHeight - element.offsetHeight - 8))}px`;
    anchor.setAttribute('aria-describedby', id);
    const outside = (event: PointerEvent) => { if (!element.contains(event.target as Node) && !anchor.contains(event.target as Node)) closeRef.current(); };
    const hide = () => closeRef.current();
    const keyboard = (event: KeyboardEvent) => {
      if ((document.activeElement as HTMLElement | null)?.closest('.ft-file-popover')) return;
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation();
        anchor.dataset.ftValueDismissed = 'true'; closeRef.current(); anchor.focus({ preventScroll: true });
      } else if (event.key === 'Tab' && !event.shiftKey && document.activeElement === anchor) {
        event.preventDefault(); event.stopPropagation(); element.querySelector('button')?.focus();
      }
    };
    window.addEventListener('resize', hide); document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', keyboard, true);
    return () => { generation.current++; clearTimeout(holdTimer.current); clearTimeout(fadeTimer.current); anchor.removeAttribute('aria-describedby'); element.hidePopover?.(); window.removeEventListener('resize', hide); document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', keyboard, true); };
  }, [anchor, id, value]);
  const label = copy === 'done' ? '已复制' : copy === 'failed' ? '复制失败，请重试' : copy === 'copying' ? '正在复制' : '复制完整值';
  const copyValue = async () => {
    if (copying.current) return;
    copying.current = true; setCopy('copying'); const ticket = generation.current;
    try {
      await navigator.clipboard.writeText(value);
      if (ticket !== generation.current) return;
      setCopy('done');
      holdTimer.current = setTimeout(() => {
        setLeaving(true);
        fadeTimer.current = setTimeout(() => {
          const restoreFocus = panel.current?.contains(document.activeElement);
          anchor.dataset.ftValueDismissed = 'true'; closeRef.current();
          if (restoreFocus && anchor.isConnected) anchor.focus({ preventScroll: true });
        }, window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 0 : 140);
      }, 1000);
    } catch {
      if (ticket !== generation.current) return;
      copying.current = false; setCopy('failed');
    }
  };
  return <div ref={panel} id={id} popover="manual" className={`ft-value-popover ${leaving ? 'is-closing' : ''}`} role="dialog" aria-label="完整内容" onClick={event => event.stopPropagation()} onPointerDown={event => event.stopPropagation()} onPointerEnter={onPointerEnter} onPointerLeave={() => { if (!copying.current && !panel.current?.contains(document.activeElement)) onPointerLeave?.(); }} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node) && !anchor.contains(event.relatedTarget as Node)) onClose(); }} onKeyDown={event => {
    event.stopPropagation();
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); anchor.dataset.ftValueDismissed = 'true'; onClose(); anchor.focus({ preventScroll: true }); }
  }}><span>{value}</span><button type="button" className={`icon-button ${copy === 'done' ? 'is-copied' : copy === 'failed' ? 'is-failed' : ''}`} aria-label={label} title={label} aria-disabled={copy === 'copying' || copy === 'done' || leaving || undefined} onClick={() => void copyValue()}><img src={`/static/ui-icons/${copy === 'done' ? 'check' : 'copy'}.svg`} width="16" height="16" alt="" /><span className="ft-sr-only" aria-live="polite">{copy === 'idle' ? '' : label}</span></button></div>;
}
