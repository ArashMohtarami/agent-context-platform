import { useState } from 'react';
import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import type { z } from 'zod';
import { useConsole, useRead } from '../management/shared';
import { Button, Empty, ErrorState, Input, Select, Skeleton } from '../components/ui';
import { page } from './contracts';

export function KnowledgeFrame({title, description, children}: {title: string; description: string; children: ReactNode}) {
  const {route, allowed} = useConsole();
  const links = [['projects', 'Projects', 'projects:read'], ['skills', 'Skills', 'skills:read'], ['memory', 'Memory', 'memory:read'], ['search', 'Search', 'search:read']];
  return <><header className="page-heading"><span className="eyebrow">Knowledge workspace</span><h1>{title}</h1><p>{description}</p></header><nav className="knowledge-tabs" aria-label="Knowledge workspace">{links.filter(([, , a]) => allowed(a!)).map(([path, label]) => <NavLink key={path} to={route + '/knowledge/' + path}>{label}</NavLink>)}</nav>{children}</>;
}
export function KnowledgePage<T>({path, schema, label, children, status = true}: {path: string; schema: z.ZodType<T>; label: string; children: (rows: T[]) => ReactNode; status?: boolean}) {
  const [cursors, setCursors] = useState<(string | null)[]>([null]); const [filter, setFilter] = useState('active');
  const params = new URLSearchParams({limit: '20'}); if (status) params.set('status', filter); const cursor = cursors.at(-1); if (cursor) params.set('cursor', cursor);
  const query = useRead(path + (path.includes('?') ? '&' : '?') + params.toString(), page(schema));
  return <>{status && <Select label={`${label} status`} value={filter} onChange={e => {setFilter(e.target.value); setCursors([null]);}}>{['active', 'archived', 'superseded'].map(s => <option key={s}>{s}</option>)}</Select>}{query.isPending ? <Skeleton label={`Loading ${label}`}/> : query.isError ? <ErrorState onRetry={() => {setCursors([null]); void query.refetch();}}>Could not load {label} within your current access.</ErrorState> : <>{query.data.items.length ? children(query.data.items) : <Empty title={`No ${label} in this view`}>No permitted records match these filters.</Empty>}<div className="pagination"><Button variant="secondary" disabled={cursors.length === 1} onClick={() => setCursors(c => c.slice(0, -1))}>Previous page</Button><span>Page {cursors.length} · up to 20 records</span><Button variant="secondary" disabled={!query.data.next_cursor} onClick={() => setCursors(c => [...c, query.data.next_cursor])}>Next page</Button></div></>}</>;
}
export function ResourceInput({action, value, onChange, optional = false}: {action: string | string[]; value: string; onChange: (value: string) => void; optional?: boolean}) {
  const {data} = useConsole(); const grants = data.grants.filter(g => (Array.isArray(action) ? action.includes(g.action) : g.action === action) && g.resource_kind === 'project');
  const all = grants.some(g => g.selector === 'all_in_org'); const ids = [...new Set(grants.filter(g => g.selector === 'include').flatMap(g => g.ids))].sort();
  return all ? <Input label="Project ID" value={value} maxLength={128} required={!optional} hint={optional ? 'Leave empty for organization-global sources and skills.' : 'An exact project ID; the server checks this action.'} onChange={e => onChange(e.target.value)}/> : <Select label="Project" value={value} onChange={e => onChange(e.target.value)} required={!optional}><option value="">{optional ? 'Organization-global sources and skills' : 'Choose an authorized project'}</option>{ids.map(p => <option key={p}>{p}</option>)}</Select>;
}
export function TextArea({label, value, onChange, required = true}: {label: string; value: string; onChange: (v: string) => void; required?: boolean}) {
  return <label className="field">{label}<textarea value={value} required={required} maxLength={12000} rows={10} onChange={e => onChange(e.target.value)}/></label>;
}
