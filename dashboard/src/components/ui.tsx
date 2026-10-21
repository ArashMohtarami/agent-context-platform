import * as Dialog from '@radix-ui/react-dialog';
import { createContext, useContext, useId, useRef } from 'react';
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from 'react';

const paths = {
  home: 'M3 10 12 3l9 7v10H3V10Zm6 10v-7h6v7',
  shield: 'M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Zm-4 9 3 3 5-6',
  key: 'M15 3a6 6 0 0 0-5 9L3 19v2h4v-3h3v-3l2-2a6 6 0 1 0 3-10Z',
  arrow: 'M5 12h14m-6-6 6 6-6 6', check: 'm5 12 4 4L19 6', close: 'm6 6 12 12M6 18 18 6',
  menu: 'M4 6h16M4 12h16M4 18h16', sun: 'M12 2v2m0 16v2M2 12h2m16 0h2M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  moon: 'M20 15A8 8 0 0 1 9 4a8 8 0 1 0 11 11Z',
  link: 'm9 15 6-6M8 12l-2 2a4 4 0 0 0 6 6l2-2M10 6l2-2a4 4 0 0 1 6 6l-2 2',
  info: 'M12 11v6m0-10v1m9 4a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  exit: 'M10 4H4v16h6m3-12 4 4-4 4m-4-4h12', users: 'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-7 9v-3c0-4 14-4 14 0v3M17 4a4 4 0 0 1 0 7m2 3c2 0 3 1 3 3v3',
};
export function Icon({name}: {name: keyof typeof paths}) {
  return <svg aria-hidden="true" viewBox="0 0 24 24" className="icon" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d={paths[name]}/></svg>;
}
interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'danger' | 'quiet'; busy?: boolean;
}
export function Button({variant = 'primary', busy = false, className = '', children, ...props}: ButtonProps) {
  return <button type="button" {...props} disabled={props.disabled || busy} aria-busy={busy || undefined} className={`button ${variant} ${className}`}>{busy ? 'Working…' : children}</button>;
}
interface FieldProps extends InputHTMLAttributes<HTMLInputElement> { label: string; hint?: string; error?: string }
export function Input({label, hint, error, id, ...props}: FieldProps) {
  const generated = useId(); const fieldId = id ?? generated;
  return <div className="field"><label htmlFor={fieldId}>{label}</label><input {...props} id={fieldId} aria-invalid={Boolean(error) || undefined} aria-describedby={hint || error ? fieldId + '-hint' : undefined}/>{(hint || error) && <p id={fieldId + '-hint'} className={error ? 'field-error' : 'hint'}>{error ?? hint}</p>}</div>;
}
interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {label: string}
export function Select({label, id, children, ...props}: SelectProps) {
  const generated = useId(); const fieldId = id ?? generated;
  return <div className="field"><label htmlFor={fieldId}>{label}</label><select {...props} id={fieldId}>{children}</select></div>;
}
export function Notice({children, tone = 'info'}: {children: ReactNode; tone?: 'info' | 'success' | 'warning' | 'danger'}) {
  return <div className={`notice ${tone}`} role={tone === 'danger' ? 'alert' : 'status'}><Icon name={tone === 'success' ? 'check' : 'info'}/><div>{children}</div></div>;
}
export function Status({children, tone = 'neutral'}: {children: ReactNode; tone?: 'neutral' | 'success' | 'warning' | 'danger'}) {
  return <span className={`status ${tone}`}><span aria-hidden="true" className="status-dot"/>{children}</span>;
}
export function Skeleton({label = 'Loading your workspace'}: {label?: string}) {
  return <div className="skeleton" role="status" aria-live="polite"><span className="sr-only">{label}</span><div/><div/><div/></div>;
}

export function WorkspaceUnavailable() {
  return <div className="error-state"><Notice tone="danger">This workspace could not load. Reload to retry with your current session.</Notice><Button variant="secondary" onClick={() => window.location.reload()}>Reload workspace</Button></div>;
}
export function Empty({title, children}: {title: string; children: ReactNode}) {
  return <div className="empty"><Icon name="info"/><h3>{title}</h3><p>{children}</p></div>;
}
export function ErrorState({children, onRetry}: {children: ReactNode; onRetry?: () => void}) {
  return <div className="error-state"><Notice tone="danger">{children}</Notice>{onRetry && <Button variant="secondary" onClick={onRetry}>Try again</Button>}</div>;
}
export function Panel({title, description, children, className = ''}: {title: string; description?: string; children: ReactNode; className?: string}) {
  return <section className={`panel ${className}`}><div className="panel-heading"><h2>{title}</h2>{description && <p>{description}</p>}</div>{children}</section>;
}
const ModalDepth = createContext(0);
export function Modal({title, description, open, onOpenChange, children, drawer = false}: {title: string; description: string; open: boolean; onOpenChange: (open: boolean) => void; children: ReactNode; drawer?: boolean}) {
  const restore = useRef<HTMLElement | null>(null); const depth = useContext(ModalDepth);
  return <ModalDepth.Provider value={depth + 1}><Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Overlay className="modal-overlay" style={{zIndex: 50 + depth * 2}}/><Dialog.Content style={{zIndex: 51 + depth * 2}} className={drawer ? 'modal drawer' : 'modal'} onOpenAutoFocus={() => {restore.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;}} onCloseAutoFocus={event => {if (restore.current?.isConnected) {event.preventDefault(); restore.current.focus();}}}><div className="modal-title"><Dialog.Title>{title}</Dialog.Title><Dialog.Close asChild><Button variant="quiet" aria-label="Close dialog"><Icon name="close"/></Button></Dialog.Close></div><Dialog.Description>{description}</Dialog.Description>{children}</Dialog.Content></Dialog.Portal></Dialog.Root></ModalDepth.Provider>;
}
export function Table({label, headings, children}: {label: string; headings: string[]; children: ReactNode}) {
  return <div className="table-scroll" role="region" aria-label={label} tabIndex={0}><table><caption className="sr-only">{label}</caption><thead><tr>{headings.map(heading => <th key={heading} scope="col">{heading}</th>)}</tr></thead><tbody>{children}</tbody></table></div>;
}
export function exactTime(value: string) {
  return new Intl.DateTimeFormat('en', {dateStyle: 'medium', timeStyle: 'medium', timeZone: 'UTC'}).format(new Date(value)) + ' UTC';
}
