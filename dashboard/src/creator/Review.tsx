import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { errorText } from '../auth/context';
import { Button, Modal, Notice, Panel } from '../components/ui';
import { id, StepUp, useConsole } from '../management/shared';
import { SafeMarkdown } from '../knowledge/Markdown';
import { Actor, Sources } from './shared';
import { approvalSchema, itemSchema, scanSchema } from './contracts';
import type { Item } from './contracts';
import type { z } from 'zod';
export function ContentReview({record, close}: {record: Item; close: () => void}) {
  const {auth, base, data, allowed} = useConsole(); const queries = useQueryClient(); const [current, setCurrent] = useState(record); const [scan, setScan] = useState<z.infer<typeof scanSchema> | null>(null); const [error, setError] = useState(''); const [busy, setBusy] = useState(false); const [stale, setStale] = useState(false);
  const who = typeof current.actor.user_id === 'string' ? current.actor.user_id : typeof current.actor.sponsor_id === 'string' ? current.actor.sponsor_id : null;
  const independent = who !== null && who !== data.me.user_id;
  const rights = allowed('content:write','creator_profile',current.profile_id) && allowed('reviews:decide','creator_profile',current.profile_id) && current.project_ids.every(p => allowed('content:write','project',p));
  const path = base + '/creator/items/' + id(current.content_id);
  return <Modal title="Review current content revision" description={`Revision ${current.revision} · body version ${current.current_version} · ${current.status}. An advisory scan does not approve this record.`} open onOpenChange={v => {if (!v && !busy) close();}}><StepUp/><h3>{current.title}</h3><SafeMarkdown>{current.body}</SafeMarkdown><Actor actor={current.actor}/><Sources refs={current.source_refs}/>{!independent && <Notice>An independent verified reviewer is required. Your own or unattributed content cannot be approved here.</Notice>}{error && <Notice tone="danger">{error} Reload the current record and review the prose again.</Notice>}<div className="actions"><Button variant="secondary" busy={busy} onClick={() => {setBusy(true); void auth.act(path + '/scan', scanSchema, {profile_id: current.profile_id}).then(setScan).catch(f => setError(errorText(f))).finally(() => setBusy(false));}}>Run advisory safety scan</Button><Button variant="secondary" onClick={() => {void auth.read(base + '/creator/profiles/' + id(current.profile_id) + '/items/' + id(current.content_id), itemSchema).then(r => {setCurrent(r); setScan(null); setError(''); setStale(false);}).catch(f => setError(errorText(f)));}}>Reload current content</Button></div>{scan && <Panel title="Advisory publication findings"><p>{scan.publishable ? 'No blocking findings detected' : 'Blocking findings need review'}</p><p>{scan.disclaimer}</p><ul>{scan.findings.map((f,i) => <li key={i}>{f.severity} · {f.finding_type} · {f.field}, {f.location}: {f.detail}</li>)}</ul></Panel>}<Button disabled={!independent || !rights || current.status !== 'review' || data.security.step_up_required || stale} busy={busy} onClick={() => {setBusy(true); void auth.act(path + '/approve', approvalSchema, {profile_id: current.profile_id, expected_revision: current.revision}).then(() => {void queries.invalidateQueries(); close();}).catch(f => {setStale(true); setError(errorText(f));}).finally(() => setBusy(false));}}>Approve revision {current.revision}</Button></Modal>;
}
