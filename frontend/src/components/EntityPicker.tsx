import { useEffect, useRef, type InputHTMLAttributes } from 'react';
import '../../../static/entity-picker.js';
import '../../../static/entity-picker.css';

type Options = Parameters<typeof window.FundsEntityPicker.create>[0];
export type EntityPickerItem = Parameters<NonNullable<Options['onSelect']>>[0];
export type EntityPickerSearch = Options['search'];

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'onInput' | 'onSubmit' | 'onSelect'> & {
  value: string;
  onValueChange(value: string): void;
  scope: string | null;
  search: EntityPickerSearch;
  onSelect(item: EntityPickerItem): void;
  onSubmit?: Options['onSubmit'];
  kind: Options['kind'];
  onCompositionChange?(composing: boolean): void;
};

/** Standalone shell adapter; shared embedded panel hosts retain their own contracts. */
export function EntityPicker({ value, onValueChange, scope, search, onSelect, onSubmit, kind, onCompositionChange, ...inputProps }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const latest = useRef({ scope, search, onSelect, onSubmit, onValueChange });
  latest.current = { scope, search, onSelect, onSubmit, onValueChange };
  useEffect(() => {
    if (!input.current || !scope) return;
    const picker = window.FundsEntityPicker.create({
      input: input.current, scope: () => latest.current.scope,
      search: request => latest.current.search(request), kind,
      onSelect: item => { latest.current.onValueChange(item.value); latest.current.onSelect(item); },
      ...(onSubmit ? { onSubmit: (...args: Parameters<NonNullable<Options['onSubmit']>>) => latest.current.onSubmit?.(...args) } : {}),
    });
    return () => { picker.destroy(); onCompositionChange?.(false); };
  }, [scope, kind, !!onSubmit]);
  // onInput also observes the core's native ghost-completion event on controlled inputs.
  return <input {...inputProps} ref={input} value={value} onChange={event => onValueChange(event.currentTarget.value)} onInput={event => onValueChange(event.currentTarget.value)} onCompositionStart={() => onCompositionChange?.(true)} onCompositionEnd={() => onCompositionChange?.(false)} />;
}
