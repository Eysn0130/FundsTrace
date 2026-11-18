import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ImportFullValue } from './ImportFullValue';

/** Fit the actual slot, retaining the extension and a useful suffix. */
export function fitFileName(value: string, available: number, measure: (text: string) => number) {
  if (measure(value) <= available) return value;
  const chars = Array.from(value), dot = value.lastIndexOf('.');
  const suffix = dot > 0 && value.length - dot <= 8 ? value.slice(dot) : '';
  let tailLength = Math.min(chars.length - 1, Math.max(8, suffix.length + 5));
  // A narrow tree still needs a recognisable prefix; a fixed long tail can
  // consume the entire slot and reduce every CSV to the extension alone.
  while (tailLength > Math.max(1, suffix.length) && measure(`${chars.slice(0, 2).join('')}…${chars.slice(-tailLength).join('')}`) > available) tailLength--;
  let low = 0, high = chars.length - tailLength;
  const tail = chars.slice(-tailLength).join('');
  while (low < high) { const middle = Math.ceil((low + high) / 2); if (measure(`${chars.slice(0, middle).join('')}…${tail}`) <= available) low = middle; else high = middle - 1; }
  if (measure(`…${tail}`) > available) return `…${suffix || chars.slice(-4).join('')}`;
  return `${chars.slice(0, low).join('')}…${tail}`;
}

export function ImportFileName({ name, path, className = '' }: { name: string; path?: string; className?: string }) {
  const ref = useRef<HTMLSpanElement>(null), [text, setText] = useState(name);
  const [open, setOpen] = useState(false), timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const clear = () => clearTimeout(timer.current), show = () => { clear(); if ((ref.current?.closest<HTMLElement>('[role="treeitem"]') || ref.current)?.dataset.ftValueDismissed) return; timer.current = setTimeout(() => setOpen(true), 320); };
  const hide = () => { clear(); setOpen(false); };
  useLayoutEffect(() => {
    const element = ref.current!;
    const canvas = document.createElement('canvas');
    const resize = () => {
      if (typeof CanvasRenderingContext2D === 'undefined') return;
      const context = canvas.getContext('2d');
      if (!context || !element.clientWidth) return;
      context.font = getComputedStyle(element).font;
      setText(fitFileName(name, element.clientWidth, value => context.measureText(value).width));
    };
    setText(name); resize();
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(resize) : undefined;
    observer?.observe(element); return () => observer?.disconnect();
  }, [name]);
  const clipped = text !== name;
  useEffect(() => {
    hide(); if (!clipped) return;
    const row = ref.current?.closest<HTMLElement>('[role="treeitem"]');
    const blur = (event: FocusEvent) => { if (!(event.relatedTarget as HTMLElement | null)?.closest('.ft-value-popover')) { if (row) delete row.dataset.ftValueDismissed; hide(); } };
    row?.addEventListener('focus', show); row?.addEventListener('blur', blur);
    const scroll = () => hide(); window.addEventListener('scroll', scroll, true);
    return () => { clear(); row?.removeEventListener('focus', show); row?.removeEventListener('blur', blur); window.removeEventListener('scroll', scroll, true); };
  }, [clipped, name, path]);
  return <><span ref={ref} className={`ft-file-name ${className}`} tabIndex={clipped && className !== 'ft-tree-name' ? 0 : undefined} aria-label={clipped ? path || name : undefined}
    onPointerEnter={() => { if (clipped) show(); }} onPointerLeave={() => { const anchor = ref.current?.closest<HTMLElement>('[role="treeitem"]') || ref.current; if (anchor) delete anchor.dataset.ftValueDismissed; clear(); timer.current = setTimeout(() => { if (!anchor?.matches(':focus')) setOpen(false); }, 160); }} onFocus={() => { if (clipped) show(); }} onBlur={event => { if (!(event.relatedTarget as HTMLElement | null)?.closest('.ft-value-popover')) { if (ref.current) delete ref.current.dataset.ftValueDismissed; hide(); } }}>{text}</span>
    {open && clipped && ref.current && <ImportFullValue anchor={ref.current.closest<HTMLElement>('[role="treeitem"]') || ref.current} value={path || name} onClose={hide} onPointerEnter={clear} onPointerLeave={hide} />}</>;
}
