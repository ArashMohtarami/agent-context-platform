import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { id, useConsole } from '../management/shared';
import type { SourceRef } from './contracts';
export { Editor, Records, ids, TextArea } from '../research/shared';
export function StudioFrame({title, children}: {title: string; children: ReactNode}) {return <><header className="page-heading"><span className="eyebrow">Creator studio</span><h1>{title}</h1><p>Plan with retained evidence, author deliberately, and review each revision before manual publication.</p></header>{children}</>;}
export function Actor({actor, label = 'Recorded actor'}: {actor: Record<string, unknown>; label?: string}) {return <p className="hint">{label}: {String(actor.actor_type ?? 'unattributed').replaceAll('_', ' ')} · {typeof actor.user_id === 'string' ? actor.user_id : typeof actor.client_id === 'string' ? actor.client_id : 'legacy unattributed'}{typeof actor.sponsor_id === 'string' && <> · event-time sponsor {actor.sponsor_id}</>}</p>;}
export function Sources({refs}: {refs: SourceRef[]}) {const {route, allowed} = useConsole(); return <ul className="creator-sources">{refs.map((r,i) => {const path = r.source_type === 'research_project' ? '/research/' + id(r.source_id) + '?tab=evidence' : r.source_type === 'skill' || r.source_type === 'active_skill' ? '/knowledge/skills/' + id(r.source_id) : r.project_id ? '/knowledge/projects/' + id(r.project_id) : '';
 const action = r.source_type === 'research_project' ? 'research:read' : r.source_type.includes('skill') ? 'skills:read' : 'context:read';
 return <li key={i}>{path && allowed(action) ? <Link to={route + path}>{r.source_type}: {r.source_id}</Link> : <span>{r.source_type}: {r.source_id}</span>}{r.project_id && <> · project {r.project_id}</>}</li>;})}</ul>;}
export const platforms = ['linkedin', 'medium', 'github'];
