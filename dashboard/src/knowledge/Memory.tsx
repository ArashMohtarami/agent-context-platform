import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../api/client';
import { errorText } from '../auth/context';
import { Button, Input, Modal, Notice, Panel, Select } from '../components/ui';
import { id, StepUp, useConsole, useDirty } from '../management/shared';
import { memorySchema } from './contracts';
import type { Memory } from './contracts';
import { KnowledgeFrame, KnowledgePage, ResourceInput, TextArea } from './shared';
import { SafeMarkdown } from './Markdown';
import { Authorship } from './Projects';

export function MemoryWorkspace() {
  const [params] = useSearchParams(); const initial = params.get('project') ?? '';
  return <MemoryView key={initial} initial={initial}/>;
}
function MemoryView({initial}: {initial: string}) {
  const {auth, base, allowed} = useConsole(); const [project, setProject] = useState(initial); const [scope, setScope] = useState(initial ? 'project' : 'global');
  const [editor, setEditor] = useState<{record: Memory | null; replace: boolean} | null>(null); const [error, setError] = useState('');
  const address = '?scope=' + scope + (scope === 'project' ? '&project_id=' + id(project) : '');
  const canRead = allowed('memory:read', scope === 'project' ? 'project' : 'organization_knowledge', scope === 'project' ? project : undefined);
  const canWrite = allowed('memory:write', scope === 'project' ? 'project' : 'organization_knowledge', scope === 'project' ? project : undefined);
  async function history(memoryId: string) {
    try {const record = await auth.read(base + '/knowledge/memories/' + id(memoryId) + address, memorySchema); setEditor({record, replace: false});}
    catch (f) {setError(errorText(f));}
  }
  return <KnowledgeFrame title="Memory" description="Intentional, durable memory. Organization-global means this organization only."><Notice>Agent and project-agent memory stay private to their verified machine client. People, team and client management access does not reveal those scopes.</Notice>
    <div className="table-toolbar"><Select label="Memory scope" value={scope} onChange={e => {setScope(e.target.value); setEditor(null); setError('');}}><option value="global">Organization-global</option><option value="project">Project</option><option disabled value="agent">Agent — machine identity required</option><option disabled value="project_agent">Project-agent — machine identity required</option></Select>{scope === 'project' && <ResourceInput action={['memory:read', 'memory:write']} value={project} onChange={p => {setProject(p); setEditor(null);}}/>}{canWrite && <Button disabled={scope === 'project' && !project} onClick={() => setEditor({record: null, replace: false})}>Create memory</Button>}</div>
    {error && <Notice tone="danger">{error}</Notice>}
    {canRead && (scope === 'global' || project) ? <Panel title={scope === 'global' ? 'Organization-global memory' : 'Memory in ' + project}><KnowledgePage key={address} path={base + '/knowledge/memories' + address} schema={memorySchema} label="memories">{rows => <div className="knowledge-cards">{rows.map(m => <article className="knowledge-card" key={m.memory_id}><div className="heading-actions"><h3>{m.summary ?? m.type + ' memory'}</h3><span className="status">{m.status} · revision {m.revision}</span></div><p className="hint">{m.scope} · source {m.source} · confidence {m.confidence}</p><SafeMarkdown>{m.content}</SafeMarkdown><Authorship actor={m.actor}/><div className="actions"><Button variant="secondary" onClick={() => setEditor({record: m, replace: false})}>Inspect memory</Button>{canWrite && m.status === 'active' && <Button variant="secondary" onClick={() => setEditor({record: m, replace: true})}>Supersede memory</Button>}{m.supersedes && <Button variant="quiet" onClick={() => {void history(m.supersedes!);}}>Previous memory</Button>}{m.superseded_by && <Button variant="quiet" onClick={() => {void history(m.superseded_by!);}}>Replacement memory</Button>}</div></article>)}</div>}</KnowledgePage></Panel> : <Notice>Choose a scope and project with explicit memory read access. Write permission is evaluated separately.</Notice>}
    {editor && <MemoryEditor key={(editor.record?.memory_id ?? 'new') + String(editor.replace)} record={editor.record} replace={editor.replace} scope={scope} project={project} writable={canWrite} address={address} close={() => setEditor(null)}/>}
  </KnowledgeFrame>;
}
function MemoryEditor({record, replace, scope, project, writable, address, close}: {record: Memory | null; replace: boolean; scope: string; project: string; writable: boolean; address: string; close: () => void}) {
  const {auth, base, data} = useConsole(); const queries = useQueryClient(); const creating = !record || replace;
  const [summary, setSummary] = useState(record?.summary ?? ''); const [content, setContent] = useState(replace ? '' : record?.content ?? ''); const [status, setStatus] = useState(record?.status ?? 'active');
  const [revision, setRevision] = useState(record?.revision ?? 1); const [latest, setLatest] = useState<Memory | null>(null); const [conflict, setConflict] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  useDirty(writable && (content !== (record?.content ?? '') || summary !== (record?.summary ?? '') || status !== (record?.status ?? 'active')));
  const path = base + '/knowledge/memories' + (creating ? '' : '/' + id(record.memory_id) + address);
  async function save() {
    setBusy(true); setError('');
    try {await auth.act(path, memorySchema, creating ? {scope, ...(scope === 'project' ? {project_id: project} : {}), summary: summary || null, content, type: record?.type ?? 'decision', importance: record?.importance ?? 0.5, confidence: record?.confidence ?? 0.8, source: 'human-workspace', ...(replace ? {supersedes: record!.memory_id} : {})} : {summary: summary || null, content, status, expected_revision: revision}, creating ? 'POST' : 'PATCH'); await queries.invalidateQueries(); close();}
    catch (f) {setConflict(f instanceof ApiError && f.status === 409); setError(f instanceof ApiError && f.status === 409 ? 'Revision or supersession conflict. Your unsaved text is preserved. Compare the current memory before continuing.' : errorText(f));}
    finally {setBusy(false);}
  }
  return <Modal title={replace ? 'Supersede memory' : creating ? 'Create memory' : 'Memory detail'} description={replace ? 'Record a correction in the same scope. The old memory and its authorship remain preserved.' : 'Memory keeps its tenant, scope and supersession links. Archive uses an expected revision.'} open onOpenChange={v => {if (!v && !busy) close();}}><StepUp/>{record && <p className="hint">ID {record.memory_id} · {record.scope} · revision {record.revision}</p>}{error && <Notice tone="danger">{error}</Notice>}{!writable ? <SafeMarkdown>{record?.content ?? ''}</SafeMarkdown> : <form onSubmit={e => {e.preventDefault(); void save();}}><Input label="Memory summary" value={summary} onChange={e => setSummary(e.target.value)}/><TextArea label="Memory Markdown" value={content} onChange={setContent}/>{!creating && <label className="check-field"><input type="checkbox" checked={status === 'archived'} onChange={e => setStatus(e.target.checked ? 'archived' : record?.status ?? 'active')}/>Archive memory</label>}{conflict && !creating && <Button variant="secondary" onClick={() => {void auth.read(path, memorySchema).then(setLatest).catch(f => setError(errorText(f)));}}>Load current memory</Button>}{latest && <Panel title={`Current server revision ${latest.revision}`}><SafeMarkdown>{latest.content}</SafeMarkdown><Button variant="secondary" onClick={() => {setRevision(latest.revision); setLatest(null); setConflict(false); setError('Current revision selected. Review your draft before saving.');}}>Use this revision with my draft</Button></Panel>}<Button type="submit" busy={busy} disabled={conflict || data.security.step_up_required}>{replace ? 'Record replacement' : 'Save memory'}</Button></form>}</Modal>;
}
