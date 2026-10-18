import { useState } from 'react';
import type { ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { z } from 'zod';
import { errorText } from '../auth/context';
import { Button, Input, Modal, Notice, Select } from '../components/ui';
import { StepUp, useConsole, useDirty } from '../management/shared';
export { KnowledgePage as Records, TextArea } from '../knowledge/shared';
export type Field = {key: string; label: string; area?: boolean; optional?: boolean; initial?: string; options?: string[]};
export function Editor<T>({title, fields, path, schema, body, close, method = 'POST'}: {title: string; fields: Field[]; path: string; schema: z.ZodType<T>; body: (values: Record<string, string>) => unknown; close: () => void; method?: 'POST' | 'PATCH'}) {
  const {auth, data} = useConsole(); const queries = useQueryClient();
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(fields.map(f => [f.key, f.initial ?? f.options?.[0] ?? ''])));
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false); useDirty(true);
  return <Modal title={title} description="Your current access and revision are checked when saving. A conflict keeps this draft; close and reopen after comparing the refreshed record." open onOpenChange={v => {if (!v && !busy) close();}}><StepUp/>{error && <Notice tone="danger">{error} Your draft is preserved.</Notice>}<form onSubmit={e => {e.preventDefault(); setError(''); setBusy(true); void Promise.resolve().then(() => auth.act(path, schema, body(values), method)).then(() => {void queries.invalidateQueries(); close();}).catch(f => {setError(errorText(f)); void queries.invalidateQueries();}).finally(() => setBusy(false));}}>{fields.map(f => f.options ? <Select key={f.key} label={f.label} required={!f.optional} value={values[f.key]} onChange={e => setValues(v => ({...v, [f.key]: e.target.value}))}>{f.options.map(o => <option key={o} value={o}>{o.replaceAll('_', ' ')}</option>)}</Select> : f.area ? <label className="field" key={f.key}>{f.label}<textarea aria-label={f.label} rows={6} maxLength={8000} required={!f.optional} value={values[f.key]} onChange={e => setValues(v => ({...v, [f.key]: e.target.value}))}/></label> : <Input key={f.key} label={f.label} required={!f.optional} value={values[f.key]} maxLength={512} onChange={e => setValues(v => ({...v, [f.key]: e.target.value}))}/>)}<Button type="submit" busy={busy} disabled={data.security.step_up_required}>Save {title.toLowerCase()}</Button></form></Modal>;
}
export function Frame({title, children}: {title: string; children: ReactNode}) {return <><header className="page-heading"><span className="eyebrow">Evidence-backed research</span><h1>{title}</h1><p>Inspect retained sources, distinguish evidence from confidence, and review before promoting durable knowledge.</p></header>{children}</>;}
export const ids = (value: string | undefined) => (value ?? '').split(',').map(v => v.trim()).filter(Boolean);

export function ResearchActor({actor}: {actor: {actor_type: string; user_id?: string | undefined}}) {return <p className="hint">Recorded actor: {actor.actor_type.replaceAll('_', ' ')} · {actor.user_id ?? 'legacy unattributed identity'}</p>;}
