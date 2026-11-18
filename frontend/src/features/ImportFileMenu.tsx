import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { ImportIcon } from './ImportTree';

export interface ImportFileCommand { label: string; icon: string; run: () => void; disabled?: boolean }
export function ImportFileMenu({ commands, information }: { commands: ImportFileCommand[]; information: ReactNode }) {
  const [open, setOpen] = useState(false), [info, setInfo] = useState(false);
  const button = useRef<HTMLButtonElement>(null), panel = useRef<HTMLDivElement>(null), id = useId();
  const close = () => { setOpen(false); panel.current?.hidePopover?.(); button.current?.focus({ preventScroll: true }); };
  useEffect(() => {
    if (!open) return;
    const pop = panel.current!, trigger = button.current!;
    pop.showPopover?.();
    const position = () => { const rect = trigger.getBoundingClientRect(); pop.style.left = `${Math.max(8, Math.min(rect.right - pop.offsetWidth, window.innerWidth - pop.offsetWidth - 8))}px`; pop.style.top = `${Math.max(8, Math.min(rect.bottom + 5, window.innerHeight - pop.offsetHeight - 8))}px`; };
    position(); pop.querySelector<HTMLElement>('button:not(:disabled)')?.focus({ preventScroll: true });
    const dismiss = (event: PointerEvent) => { if (!pop.contains(event.target as Node) && !trigger.contains(event.target as Node)) setOpen(false); };
    document.addEventListener('pointerdown', dismiss); window.addEventListener('resize', position);
    return () => { pop.hidePopover?.(); document.removeEventListener('pointerdown', dismiss); window.removeEventListener('resize', position); };
  }, [open, info]);
  return <><button ref={button} type="button" className="ft-more icon-button" aria-label="文件操作" aria-haspopup="menu" aria-expanded={open} aria-controls={id} onClick={() => { setInfo(false); setOpen(value => !value); }}><ImportIcon name="dots" /></button>
    {open && <div ref={panel} id={id} className={`ft-file-popover ${info ? 'is-info' : ''}`} popover="manual" role={info ? 'dialog' : 'menu'} aria-label={info ? '文件信息' : '文件操作'} onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
      else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) { event.preventDefault(); const controls = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')], index = controls.indexOf(document.activeElement as HTMLButtonElement); controls[event.key === 'Home' ? 0 : event.key === 'End' ? controls.length - 1 : (index + (event.key === 'ArrowUp' ? -1 : 1) + controls.length) % controls.length]?.focus(); }
      else if (event.key === 'Tab') { event.preventDefault(); close(); }
    }}>{info ? <><button type="button" className="ft-menu-back" onClick={() => setInfo(false)}><ImportIcon name="chevron-left" />文件信息</button>{information}</> : <>{commands.map(command => <button key={command.label} type="button" role="menuitem" disabled={command.disabled} onClick={() => { close(); command.run(); }}><ImportIcon name={command.icon} />{command.label}</button>)}<div className="ft-menu-divider" /><button type="button" role="menuitem" onClick={() => setInfo(true)}><ImportIcon name="info-circle" />文件信息</button></>}</div>}</>;
}
