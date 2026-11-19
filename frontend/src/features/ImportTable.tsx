import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import '../../../static/table-scroll.js';
import { ImportIcon } from './ImportTree';
import { ImportFullValue } from './ImportFullValue';

declare global {
  interface Window {
    FundsTableScroll: { bind(elements: { viewport: HTMLElement; range: HTMLInputElement; left: HTMLButtonElement; right: HTMLButtonElement; table: HTMLTableElement }): { sync(): void; destroy(): void } | undefined };
  }
}

export function ImportCellValue({ children, fullValue }: { children: ReactNode; fullValue?: string }) {
  return <span className="ft-cell-value" data-full-value={fullValue} tabIndex={fullValue === undefined ? -1 : 0}>{children ?? '—'}</span>;
}

const minimumManualWidth = 64;
const sampleLimit = 50;
const moneySampleLimit = 120; // Calendar pages contain at most 120 periods.
const noMoneyColumns: number[] = [];
/** Fill spare space without shrinking content widths or changing manual columns. */
export function fillColumnWidths(widths: number[], available: number, fixed: ReadonlySet<number>) {
  const extra = Math.floor(available) - widths.reduce((sum, width) => sum + width, 0);
  const flexible = widths.flatMap((_width, index) => fixed.has(index) ? [] : [index]);
  if (extra <= 0 || !flexible.length) return widths;
  const share = Math.floor(extra / flexible.length), remainder = extra % flexible.length;
  return widths.map((width, index) => {
    const position = flexible.indexOf(index);
    return position < 0 ? width : width + share + (position < remainder ? 1 : 0);
  });
}
function contentWidth(cell: HTMLTableCellElement, range: Range) {
  range.selectNodeContents(cell.hasAttribute('data-fit-cell') ? cell : cell.querySelector('.ft-cell-value') || cell);
  const style = getComputedStyle(cell);
  // Range uses the rendered font and tabular digits, unlike a canvas estimate.
  return Math.ceil(range.getBoundingClientRect().width + parseFloat(style.paddingLeft) + parseFloat(style.paddingRight) + parseFloat(style.borderLeftWidth || '0') + parseFloat(style.borderRightWidth || '0') + parseFloat(cell.dataset.fitExtra || '0')) + 2;
}
function loadedColumnWidth(table: HTMLTableElement, index: number, rowLimit = sampleLimit, cap = Infinity, automatic = false, textMaxWidth = 360) {
  const range = document.createRange();
  if (typeof range.getBoundingClientRect !== 'function') return undefined;
  const headerWidth = contentWidth(table.tHead!.rows[0].cells[index], range);
  let width = Math.max(minimumManualWidth, headerWidth);
  const seen = new Set<string>();
  const rows = table.tBodies[0].rows;
  for (let row = 0; row < Math.min(rows.length, rowLimit); row++) {
    const cell = rows[row].cells[index];
    if (!cell || cell.colSpan > 1) continue;
    const text = cell.textContent || '';
    if (seen.has(text)) continue;
    seen.add(text); width = Math.max(width, Math.min(contentWidth(cell, range), automatic && cell.hasAttribute('data-fit-text') ? Math.min(cap,textMaxWidth) : cap));
    if (width >= cap) break;
  }
  return Math.min(width, Math.max(cap, headerWidth));
}

/** The import grids share column, overflow and full-value behavior. */
export function ImportTable({ headings, widths: initial, children, className, label, moneyColumns = noMoneyColumns, fillToSlot = false, resizable = true, autoSize = false, textMaxWidth = 360, revealAfterFit = false }: {
  headings: string[]; widths: number[]; children: ReactNode; className: string; label: string; moneyColumns?: number[]; fillToSlot?: boolean; resizable?: boolean; autoSize?: boolean; textMaxWidth?: number; revealAfterFit?: boolean;
}) {
  const scroll = useRef<HTMLDivElement>(null), table = useRef<HTMLTableElement>(null);
  const range = useRef<HTMLInputElement>(null), left = useRef<HTMLButtonElement>(null), right = useRef<HTMLButtonElement>(null);
  const [widths, setWidths] = useState(initial), [value, setValue] = useState('');
  const [firstFitReady, setFirstFitReady] = useState(false);
  const firstFit = revealAfterFit ? firstFitReady ? 'ready' : autoSize ? 'sizing' : 'pending' : undefined;
  const previousHeadings = useRef(headings);
  const headingKey = JSON.stringify(headings);
  const [owner, setOwner] = useState<HTMLElement | null>(null), timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const cancelResize = useRef<(() => void) | undefined>(undefined);
  const manuallySized = useRef(new Set<number>()), measured = useRef('');
  const intrinsicWidths = useRef<number[]>([]);
  const previousWidths = useRef<number[]>([]), previousChildren = useRef<ReactNode>(null), overflowColumns = useRef(new Set<number>());
  const hide = () => { clearTimeout(timer.current); setOwner(null); };
  useEffect(() => () => { clearTimeout(timer.current); cancelResize.current?.(); }, []);
  useLayoutEffect(() => { if (owner && !owner.isConnected) hide(); }, [children, owner]);
  useLayoutEffect(() => {
    const previous = previousHeadings.current;
    if (JSON.stringify(previous) === headingKey) return;
    const oldManual = manuallySized.current, manual = new Set<number>();
    headings.forEach((heading,index) => { if (oldManual.has(previous.indexOf(heading))) manual.add(index); });
    setWidths(current => headings.map((heading,index) => current[previous.indexOf(heading)] ?? initial[index]));
    manuallySized.current = manual; previousHeadings.current = headings; measured.current = ''; intrinsicWidths.current = [];
  }, [headingKey]);
  useLayoutEffect(() => window.FundsTableScroll.bind({ viewport: scroll.current!, range: range.current!, left: left.current!, right: right.current!, table: table.current! })?.destroy, []);
  useLayoutEffect(() => {
    if (!fillToSlot || autoSize) return;
    const fill = () => {
      const available = scroll.current!.clientWidth;
      if (!available || manuallySized.current.size) return;
      const total = initial.reduce((sum, width) => sum + width, 0), next = initial.map(width => Math.max(80, Math.floor(available * width / total)));
      if (available >= 80 * next.length) next[next.length - 1] += available - next.reduce((sum, width) => sum + width, 0);
      setWidths(current => next.some((width, index) => width !== current[index]) ? next : current);
    };
    fill(); const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(fill) : undefined;
    observer?.observe(scroll.current!); return () => observer?.disconnect();
  }, [fillToSlot, autoSize]);
  useLayoutEffect(() => {
    if (!fillToSlot || !autoSize) return;
    const fit = () => setWidths(current => {
      const base = current.map((width, index) => manuallySized.current.has(index) ? width : intrinsicWidths.current[index] ?? width);
      const next = fillColumnWidths(base, scroll.current?.clientWidth || 0, manuallySized.current);
      return next.some((width, index) => width !== current[index]) ? next : current;
    });
    fit(); const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(fit) : undefined;
    observer?.observe(scroll.current!); return () => observer?.disconnect();
  }, [fillToSlot, autoSize, headingKey]);
  useLayoutEffect(() => {
    widths.forEach((width, index) => { if (width !== previousWidths.current[index] || children !== previousChildren.current) overflowColumns.current.add(index); });
    previousWidths.current = widths; previousChildren.current = children;
    const frame = requestAnimationFrame(() => {
      for (const index of overflowColumns.current) for (const cell of table.current?.querySelectorAll<HTMLElement>(`td:nth-child(${index + 1}) .ft-cell-value`) ?? []) cell.tabIndex = cell.hasAttribute('data-full-value') || cell.scrollWidth > cell.clientWidth + 1 ? 0 : -1;
      overflowColumns.current.clear();
    });
    return () => cancelAnimationFrame(frame);
  }, [children, widths]);
  useEffect(() => {
    if (!autoSize || !table.current) return;
    const rows = table.current.tBodies[0].rows;
    const signature = JSON.stringify(Array.from({ length: Math.min(rows.length, moneyColumns.length ? moneySampleLimit : sampleLimit) }, (_, row) => row < sampleLimit
      ? [...rows[row].cells].map(cell => cell.textContent)
      : moneyColumns.map(index => rows[row].cells[index]?.textContent)));
    if (measured.current === signature && (!revealAfterFit || firstFitReady)) return;
    const frame = requestAnimationFrame(() => {
      if (!table.current) return;
      const cap = Math.max(240, Math.min(360, (scroll.current?.clientWidth || 720) / 2));
      const natural = headings.map((_heading, index) => loadedColumnWidth(table.current!, index, moneyColumns.includes(index) ? moneySampleLimit : sampleLimit, index < 2 || moneyColumns.includes(index) ? Infinity : cap,true,textMaxWidth));
      measured.current = signature;
      setWidths(current => {
        intrinsicWidths.current = natural.map((width, index) => width ?? current[index]);
        const next = current.map((width, index) => manuallySized.current.has(index) ? width : intrinsicWidths.current[index]);
        return fillToSlot ? fillColumnWidths(next, scroll.current?.clientWidth || 0, manuallySized.current) : next;
      });
      if (revealAfterFit && !firstFitReady) setFirstFitReady(true);
    });
    return () => cancelAnimationFrame(frame);
  }, [children, autoSize, headings, moneyColumns, textMaxWidth, fillToSlot, revealAfterFit, firstFitReady]);
  const bounded = (width: number) => Math.min(Number.MAX_SAFE_INTEGER, Math.max(minimumManualWidth, Math.round(width)));
  const resize = (index: number, width: number) => { manuallySized.current.add(index); setWidths(current => {
    const next = current.map((value, i) => i === index ? bounded(width) : fillToSlot && autoSize && !manuallySized.current.has(i) ? intrinsicWidths.current[i] ?? value : value);
    return fillToSlot && autoSize ? fillColumnWidths(next, scroll.current?.clientWidth || 0, manuallySized.current) : next;
  }); };
  const show = (target: EventTarget) => {
    const element = target as HTMLElement;
    const cell = element.closest<HTMLElement>('.ft-cell-value') || element.closest('td')?.querySelector<HTMLElement>('.ft-cell-value[data-full-value]');
    clearTimeout(timer.current);
    if (cell?.dataset.ftValueDismissed) return;
    if (!cell || !cell.hasAttribute('data-full-value') && cell.scrollWidth <= cell.clientWidth + 1) { hide(); return; }
    timer.current = setTimeout(() => { if (cell.isConnected) { setValue(cell.dataset.fullValue ?? cell.textContent ?? ''); setOwner(cell); } }, 320);
  };
  return <><div className="ft-grid-shell"><div ref={scroll} className={`${className} ft-grid-scroll`} role="region" tabIndex={0} aria-label={label} onScroll={event => {
    hide();
    const focused = document.activeElement as HTMLElement | null;
    if (focused?.matches('.ft-cell-value') && event.currentTarget.contains(focused)) {
      const cell = focused.getBoundingClientRect(), viewport = event.currentTarget.getBoundingClientRect();
      if (cell.right > viewport.left && cell.left < viewport.right && cell.bottom > viewport.top + (table.current?.tHead?.getBoundingClientRect().height || 0) && cell.top < viewport.bottom) show(focused);
    }
  }}
    onPointerOver={event => show(event.target)} onFocus={event => show(event.target)} onBlur={event => { if (!(event.relatedTarget as HTMLElement | null)?.closest('.ft-value-popover')) { delete (event.target as HTMLElement).dataset.ftValueDismissed; hide(); } }}
    onPointerOut={event => { if (!(event.relatedTarget as HTMLElement | null)?.closest('.ft-value-popover')) { delete (event.target as HTMLElement).dataset.ftValueDismissed; clearTimeout(timer.current); timer.current = setTimeout(hide, 160); } }}>
    <table ref={table} className={`ft-data-grid ${resizable ? '' : 'ft-static-grid'}`} data-first-fit={firstFit} style={{ width: widths.reduce((sum, width) => sum + width, 0) }}><colgroup>{widths.map((width, index) => <col key={index} style={{ width }} />)}</colgroup><thead><tr>{headings.map((heading, index) => <th key={`${heading}:${index}`} scope="col"><span className="ft-cell-value">{heading}</span>{resizable && <button type="button" className="ft-column-resize" role="separator" aria-orientation="vertical" aria-label={`调整${heading}列宽`} aria-valuenow={widths[index]} aria-valuemin={minimumManualWidth} aria-valuemax={Number.MAX_SAFE_INTEGER} aria-valuetext={`${widths[index]} 像素`}
      onKeyDown={event => { if (['ArrowLeft','ArrowRight','Home'].includes(event.key) && !event.ctrlKey && !event.metaKey && !event.altKey) { event.preventDefault(); hide(); resize(index, event.key === 'Home' ? minimumManualWidth : widths[index] + (event.key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 1 : 8)); } }}
      onDoubleClick={() => { const natural = loadedColumnWidth(table.current!, index, moneyColumns.includes(index) ? moneySampleLimit : sampleLimit); if (natural !== undefined) resize(index, natural); }}
      onPointerDown={event => {
        if (event.button !== 0) return;
        event.preventDefault(); hide(); cancelResize.current?.();
        const startX = event.clientX, start = widths[index], handle = event.currentTarget, wasManual = manuallySized.current.has(index);
        const move = (next: PointerEvent) => resize(index, start + next.clientX - startX);
        const finish = (next?: Event) => { if (next?.type === 'pointercancel' || next instanceof KeyboardEvent && next.key === 'Escape') { resize(index, start); if (!wasManual) manuallySized.current.delete(index); } handle.classList.remove('is-resizing'); window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', finish); window.removeEventListener('pointercancel', finish); window.removeEventListener('keydown', escape, true); cancelResize.current = undefined; };
        const escape = (next: KeyboardEvent) => { if (next.key === 'Escape') { next.preventDefault(); next.stopPropagation(); finish(next); } };
        cancelResize.current = finish; handle.classList.add('is-resizing'); window.addEventListener('pointermove', move); window.addEventListener('pointerup', finish, { once: true }); window.addEventListener('pointercancel', finish, { once: true }); window.addEventListener('keydown', escape, true);
      }}><ImportIcon name="grip-vertical" /></button>}</th>)}</tr></thead>{children}</table>
  </div><div className="ft-horizontal-scroll"><button ref={left} type="button" aria-label={`${headings.length}列表格向左滚动`}><ImportIcon name="chevron-left" /></button><input ref={range} type="range" min="0" max="0" defaultValue="0" step="1" aria-label={`${headings.length}列表格水平滚动`} /><button ref={right} type="button" aria-label={`${headings.length}列表格向右滚动`}><ImportIcon name="chevron-right" /></button></div></div>{owner && <ImportFullValue anchor={owner} value={value} onClose={hide} onPointerEnter={() => clearTimeout(timer.current)} onPointerLeave={hide} />}</>;
}
