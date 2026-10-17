import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Button, ErrorState, Input, Modal, Notice, Panel, Skeleton, Table } from '../components/ui';
import { ApiError } from '../api/client';
import { errorText } from '../auth/context';
import { id, StepUp, useConsole, useDirty, useRead } from '../management/shared';
import { contextSchema, projectSchema } from './contracts';
import type { Context } from './contracts';
import { KnowledgeFrame, KnowledgePage, TextArea } from './shared';
import { SafeMarkdown } from './Markdown';

export function Projects() {
  const {auth, base, route, allowed, data} = useConsole(); const queries = useQueryClient();
  const [open, setOpen] = useState(false); const [project, setProject] = useState(''); const [name, setName] = useState(''); const [description, setDescription] = useState(''); const [repository, setRepository] = useState(''); const [environment, setEnvironment] = useState('development');
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false); useDirty(open && Boolean(project || name || description));
  return <KnowledgeFrame title="Projects" description="Project boundaries and durable knowledge permitted by your current assignments.">
    {allowed('projects:write') && <div className="actions"><Button onClick={() => setOpen(true)}>Create project</Button></div>}
    <Panel title="Project directory"><KnowledgePage path={base + '/knowledge/projects'} schema={projectSchema} label="projects" status={false}>{rows => <Table label="Authorized projects" headings={['Project', 'Description', 'Environment']}>{rows.map(p => <tr key={p.project_id}><td><Link to={route + '/knowledge/projects/' + id(p.project_id)}>{p.name}</Link><small>{p.project_id}</small></td><td>{p.description}</td><td>{p.environment}</td></tr>)}</Table>}</KnowledgePage></Panel>
    <Modal title="Create project" description="Create a project only within your projects:write assignment. This does not grant new corpus access." open={open} onOpenChange={v => {if (!busy) {setOpen(v); if (!v) {setProject(''); setName(''); setDescription(''); setRepository(''); setError('');}}}}><StepUp/>{error && <Notice tone="danger">{error}</Notice>}<form onSubmit={e => {e.preventDefault(); setBusy(true); setError(''); void auth.act(base + '/knowledge/projects', projectSchema, {project_id: project, name, description, repository, environment}).then(() => {setOpen(false); setProject(''); setName(''); setDescription(''); setRepository(''); void queries.invalidateQueries();}).catch(f => setError(errorText(f))).finally(() => setBusy(false));}}><Input label="Project ID" value={project} required maxLength={128} onChange={e => setProject(e.target.value)}/><Input label="Project name" value={name} required onChange={e => setName(e.target.value)}/><TextArea label="Project description" value={description} onChange={setDescription}/><Input label="Repository reference" value={repository} required onChange={e => setRepository(e.target.value)}/><Input label="Environment" value={environment} required onChange={e => setEnvironment(e.target.value)}/><Button type="submit" busy={busy} disabled={data.security.step_up_required || !allowed('projects:write', 'project_directory', project)}>Create project</Button></form></Modal>
  </KnowledgeFrame>;
}
export function ProjectDetail() {
  const {projectId = ''} = useParams(); return <ProjectWorkspace key={projectId} project={projectId}/>;
}
function ProjectWorkspace({project}: {project: string}) {
  const {base, route, allowed} = useConsole(); const info = useRead(base + '/knowledge/projects/' + id(project), projectSchema);
  const [edit, setEdit] = useState<Context | null | undefined>(undefined);
  return <KnowledgeFrame title={info.data?.name ?? 'Project details'} description="Project context and links to action-specific memory and search.">
    {info.isPending ? <Skeleton/> : info.isError ? <ErrorState>Project details are outside your current directory access or unavailable.</ErrorState> : <Panel title={info.data.project_id}><p>{info.data.description}</p><dl className="detail-list"><div><dt>Repository reference</dt><dd>{info.data.repository}</dd></div><div><dt>Environment</dt><dd>{info.data.environment}</dd></div></dl></Panel>}
    <div className="actions">{allowed('memory:read', 'project', project) && <Link className="button secondary" to={route + '/knowledge/memory?project=' + id(project)}>Project memory</Link>}{allowed('search:read', 'project', project) && <Link className="button secondary" to={route + '/knowledge/search?project=' + id(project)}>Search this project</Link>}{allowed('context:write', 'project', project) && <Button onClick={() => setEdit(null)}>Record context</Button>}</div>
    {allowed('context:read', 'project', project) ? <Panel title="Project context" description="Context keeps the current text and revision number. Earlier text snapshots are unavailable."><KnowledgePage path={base + '/knowledge/context?project_id=' + id(project)} schema={contextSchema} label="context">{rows => <div className="knowledge-cards">{rows.map(c => <article className="knowledge-card" key={c.context_id}><div className="heading-actions"><h3>{c.title}</h3><span className="status">{c.status} · revision {c.revision}</span></div><p className="hint">{c.type} · source {c.source}</p><SafeMarkdown>{c.content}</SafeMarkdown><Authorship actor={c.actor}/>{c.promotion_provenance && <p className="hint">Promoted knowledge · provenance recorded</p>}{allowed('context:write', 'project', project) && <Button variant="secondary" onClick={() => setEdit(c)}>Edit {c.title}</Button>}</article>)}</div>}</KnowledgePage></Panel> : <Notice>Context read access has not been assigned for this project.</Notice>}
    {edit !== undefined && <ContextEditor project={project} record={edit} close={() => setEdit(undefined)}/>}
  </KnowledgeFrame>;
}
export function Authorship({actor}: {actor: Record<string, unknown>}) {
  const kind = typeof actor.actor_type === 'string' ? actor.actor_type : 'unattributed';
  return <p className="hint">Authorship: {kind.replaceAll('_', ' ')}{typeof actor.user_id === 'string' ? ' · ' + actor.user_id : kind === 'human' ? ' · legacy unattributed identity' : typeof actor.client_id === 'string' ? ' · ' + actor.client_id : ''}</p>;
}
function ContextEditor({project, record, close}: {project: string; record: Context | null; close: () => void}) {
  const {auth, base, data} = useConsole(); const queries = useQueryClient();
  const [title, setTitle] = useState(record?.title ?? ''); const [content, setContent] = useState(record?.content ?? ''); const [status, setStatus] = useState(record?.status ?? 'active'); const [revision, setRevision] = useState(record?.revision ?? 1);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [conflict, setConflict] = useState(false); const [latest, setLatest] = useState<Context | null>(null);
  useDirty(title !== (record?.title ?? '') || content !== (record?.content ?? '') || status !== record?.status && record !== null);
  const path = base + '/knowledge/context' + (record ? '/' + id(record.context_id) + '?project_id=' + id(project) : '');
  async function submit() {
    setBusy(true); setError('');
    try {await auth.act(path, contextSchema, record ? {title, content, status, expected_revision: revision} : {project_id: project, type: 'convention', title, content, importance: 0.5, source: 'human-workspace'} , record ? 'PATCH' : 'POST'); await queries.invalidateQueries(); close();}
    catch (failure) {setConflict(failure instanceof ApiError && failure.status === 409); setError(failure instanceof ApiError && failure.status === 409 ? 'Revision conflict. Your unsaved text is preserved. Load the current record and compare before saving again.' : errorText(failure));}
    finally {setBusy(false);}
  }
  return <Modal title={record ? 'Edit context' : 'Record context'} description="Write intentional durable knowledge. Saving checks your current resource policy, MFA and expected revision." open onOpenChange={v => {if (!v && !busy) close();}}><StepUp/>{error && <Notice tone="danger">{error}</Notice>}<form onSubmit={e => {e.preventDefault(); void submit();}}><Input label="Context title" value={title} required onChange={e => setTitle(e.target.value)}/><TextArea label="Context Markdown" value={content} onChange={setContent}/>{record && <label className="check-field"><input type="checkbox" checked={status === 'archived'} onChange={e => setStatus(e.target.checked ? 'archived' : 'active')}/>Archive context</label>}{conflict && record && <Button variant="secondary" onClick={() => {void auth.read(path, contextSchema).then(setLatest).catch(f => setError(errorText(f)));}}>Load current revision</Button>}{latest && <Panel title={`Current server revision ${latest.revision}`}><h3>{latest.title}</h3><SafeMarkdown>{latest.content}</SafeMarkdown><Button variant="secondary" onClick={() => {setRevision(latest.revision); setConflict(false); setLatest(null); setError('Review complete. Your draft will be saved against the loaded revision.');}}>Use this revision with my draft</Button></Panel>}<Button type="submit" busy={busy} disabled={conflict || data.security.step_up_required}>Save context</Button></form></Modal>;
}
