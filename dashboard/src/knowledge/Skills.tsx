import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Button, ErrorState, Panel, Select, Skeleton, Table } from '../components/ui';
import { id, useConsole, useRead } from '../management/shared';
import { skillDetailSchema, skillSchema } from './contracts';
import { KnowledgeFrame, KnowledgePage } from './shared';
import { SafeMarkdown } from './Markdown';

export function Skills() {
  const {base, route} = useConsole();
  return <KnowledgeFrame title="Skills" description="Read imported canonical Git skills, their trust labels and preserved versions."><Panel title="Skill catalog"><KnowledgePage path={base + '/knowledge/skills'} schema={skillSchema} label="skills">{rows => <Table label="Authorized canonical skills" headings={['Skill', 'Version', 'Trust', 'Category']}>{rows.map(s => <tr key={s.skill_id + s.version}><td><Link to={route + '/knowledge/skills/' + id(s.skill_id)}>{s.name}</Link><small>{s.skill_id}</small></td><td>{s.version}</td><td>{s.trust_level}</td><td>{s.category}</td></tr>)}</Table>}</KnowledgePage></Panel></KnowledgeFrame>;
}
export function SkillDetail() {const {skillId = ''} = useParams(); return <SkillView key={skillId} skillId={skillId}/>;}
function SkillView({skillId}: {skillId: string}) {
  const {base} = useConsole(); const [version, setVersion] = useState(''); const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const params = new URLSearchParams({chunk_limit: '20'}); if (version) params.set('version', version); const cursor = cursors.at(-1); if (cursor) params.set('chunk_cursor', cursor);
  const query = useRead(base + '/knowledge/skills/' + id(skillId) + '?' + params.toString(), skillDetailSchema);
  return <KnowledgeFrame title={query.data?.skill.name ?? 'Skill review'} description="Canonical Git is the authoring source. This view cannot edit server files.">{query.isPending ? <Skeleton/> : query.isError ? <ErrorState onRetry={() => {setCursors([null]); void query.refetch();}}>Skill or version is outside your current access or unavailable.</ErrorState> : <><Panel title={query.data.skill.skill_id}><p>{query.data.skill.description}</p><dl className="detail-list"><div><dt>Trust</dt><dd>{query.data.skill.trust_level}</dd></div><div><dt>Canonical source</dt><dd>{query.data.skill.source_path}</dd></div><div><dt>Content hash</dt><dd><code>{query.data.skill.content_hash}</code></dd></div></dl><Select label="Skill version" value={version} onChange={e => {setVersion(e.target.value); setCursors([null]);}}><option value="">Current active version</option>{query.data.versions.map(v => <option key={v.version} value={v.version}>{v.version} · {v.status}</option>)}</Select></Panel><Panel title={`Version ${query.data.skill.version} · ${query.data.skill.status}`}>{query.data.chunks.map(c => <article className="knowledge-card" key={c.chunk_id}><h3>{c.section ?? c.chunk_id}</h3><p className="hint">{c.tokens} estimated tokens</p><SafeMarkdown>{c.content}</SafeMarkdown></article>)}<div className="pagination"><Button variant="secondary" disabled={cursors.length === 1} onClick={() => setCursors(c => c.slice(0, -1))}>Previous sections</Button><Button variant="secondary" disabled={!query.data.next_chunk_cursor} onClick={() => setCursors(c => [...c, query.data.next_chunk_cursor])}>Next sections</Button></div></Panel><Panel title="Version history"><Table label="Canonical skill versions" headings={['Version', 'Status', 'Content hash']}>{query.data.versions.map(v => <tr key={v.version}><td>{v.version}</td><td>{v.status}</td><td><code>{v.content_hash}</code></td></tr>)}</Table></Panel></>}</KnowledgeFrame>;
}
