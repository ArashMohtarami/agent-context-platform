import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import type { z } from 'zod';
import { namespace } from '../api/client';
import { voidSchema } from '../api/contracts';
import { errorText, useAuth } from '../auth/context';
import { Button, Empty, ErrorState, Input, Modal, Notice, Select, Skeleton } from '../components/ui';
import { contextSchema, pageSchema } from './contracts';
import type { Grant } from './contracts';

export const id = encodeURIComponent;
export function useConsole() {
  const auth = useAuth();
  if (auth.state.status !== 'active') throw new Error('Current organization required');
  const data = auth.state.data;
  const org = data.me.policy_context.org_id;
  return {auth, data, org, base: '/organizations/' + id(org), route: '/o/' + id(org),
    allowed: (action: string, kind?: string, resource?: string) => data.grants.some(g => g.action === action && g.selector !== 'none' && (!kind || g.resource_kind === kind) && (!resource || g.selector === 'all_in_org' || g.ids.includes(resource)))};
}
export function useRead<T>(path: string, schema: z.ZodType<T>, enabled = true) {
  const {auth, data} = useConsole();
  return useQuery({queryKey: [...namespace(data, auth.generation), path], queryFn: ({signal}) => auth.read(path, schema, signal), enabled});
}
export function useAuthoring() {const {base} = useConsole(); return useRead(base + '/management/context', contextSchema);}
export function useCommand() {
  const {auth} = useConsole(); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  async function run(work: () => Promise<unknown>, refresh = true) {
    setBusy(true); setError('');
    try {await work(); if (refresh) await auth.refresh(); return true;}
    catch (failure) {setError(errorText(failure)); return false;}
    finally {setBusy(false);}
  }
  return {busy, error, run, clear: () => setError('')};
}
export function StepUp() {
  const {data, route} = useConsole();
  return data.security.step_up_required ? <Notice tone="warning">Recent MFA is required for changes. <Link to={route + '/security'}>Verify in My security</Link>, then return and review the latest revision.</Notice> : null;
}
export function Heading({title, description, children}: {title: string; description: string; children?: ReactNode}) {
  return <header className="page-heading"><span className="eyebrow">Organization management</span><div className="heading-actions"><div><h1>{title}</h1><p>{description}</p></div>{children}</div></header>;
}
export function GrantTable({grants, label = 'Action and resource policy'}: {grants: Grant[]; label?: string}) {
  return grants.length === 0 ? <p className="hint">NONE — no action or resource grants.</p> : <ul className="grant-summary" aria-label={label}>{grants.map((g, i) => <li key={i}><code>{g.action}</code><span>{g.resource_kind.replaceAll('_', ' ')}</span><strong>{g.selector === 'none' ? 'NONE' : g.selector === 'all_in_org' ? 'ALL IN THIS ORGANIZATION' : g.ids.join(', ')}</strong></li>)}</ul>;
}
export function ConfirmAction({label, description, path, body, disabled = false, danger = false, method = 'POST'}: {label: string; description: string; path: string; body: unknown; disabled?: boolean; danger?: boolean; method?: 'POST' | 'PATCH' | 'PUT'}) {
  const {auth, data} = useConsole(); const command = useCommand(); const [open, setOpen] = useState(false);
  return <><Button variant={danger ? 'danger' : 'secondary'} disabled={disabled || data.security.step_up_required} onClick={() => setOpen(true)}>{label}</Button><Modal title={label + '?'} description={description} open={open} onOpenChange={value => {if (!command.busy) setOpen(value);}}>
    <StepUp/>{command.error && <Notice tone="danger">{command.error}</Notice>}<div className="actions"><Button variant="secondary" disabled={command.busy} onClick={() => setOpen(false)}>Cancel</Button><Button variant={danger ? 'danger' : 'primary'} busy={command.busy} onClick={() => {void command.run(() => auth.act(path, voidSchema, body, method));}}>Confirm {label.toLowerCase()}</Button></div></Modal></>;
}
export function Paged<T>({path, schema, label, children, filter = true, statuses = ['active', 'disabled', 'revoked', 'ended', 'expired'], prefixLabel}: {path: string; schema: z.ZodType<T>; label: string; children: (rows: T[], revision: number) => ReactNode; filter?: boolean; statuses?: string[]; prefixLabel?: string}) {
  const [prefix, setPrefix] = useState(''); const [status, setStatus] = useState(''); const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const cursor = cursors.at(-1); const params = new URLSearchParams({limit: '25'});
  if (prefix) params.set('prefix', prefix); if (status) params.set('status', status); if (cursor) params.set('cursor', cursor);
  const query = useRead(path + (path.includes('?') ? '&' : '?') + params.toString(), pageSchema(schema));
  return <>{filter && <div className="table-toolbar"><Input label={prefixLabel ?? `Filter ${label} by ID prefix`} value={prefix} maxLength={128} onChange={e => {setPrefix(e.target.value); setCursors([null]);}}/>{statuses.length > 0 && <Select label="Status filter" value={status} onChange={e => {setStatus(e.target.value); setCursors([null]);}}><option value="">All statuses</option>{statuses.map(s => <option key={s}>{s}</option>)}</Select>}</div>}
    {query.isPending ? <Skeleton label={`Loading ${label}`}/> : query.isError ? <ErrorState onRetry={() => {setCursors([null]); void query.refetch();}}>Could not load {label}. Refresh after a policy or revision change.</ErrorState> : <>{query.data.items.length ? children(query.data.items, query.data.policy_revision) : <Empty title={`No ${label} in this view`}>No permitted records match the current filters.</Empty>}<div className="pagination"><Button variant="secondary" disabled={cursors.length === 1} onClick={() => setCursors(c => c.slice(0, -1))}>Previous page</Button><span>Page {cursors.length} · up to 25 records</span><Button variant="secondary" disabled={!query.data.next_cursor} onClick={() => setCursors(c => [...c, query.data.next_cursor])}>Next page</Button></div></>}
  </>;
}
export function useDirty(dirty: boolean) {const {auth: {setDirty}} = useConsole(); useEffect(() => {setDirty(dirty); return () => setDirty(false);}, [setDirty, dirty]);}
