import { useState } from 'react';
import type { FormEvent } from 'react';
import { voidSchema } from '../api/contracts';
import { Button, Input, Modal, Notice, Select } from '../components/ui';
import { GrantTable, StepUp, useAuthoring, useCommand, useConsole, useDirty } from '../management/shared';
import { previewSchema } from '../management/contracts';
import type { Grant, Reviewed } from '../management/contracts';

export function identifiers(text: string) {return text.split(',').map(s => s.trim()).filter(Boolean);}
export function IDsInput({label, value, onChange}: {label: string; value: string[]; onChange: (ids: string[]) => void}) {
  const [draft, setDraft] = useState(value.join(', '));
  return <Input label={label} value={draft} placeholder="Comma-separated exact IDs" onChange={e => {setDraft(e.target.value); onChange(identifiers(e.target.value));}}/>;
}
export function utcInput(hours = 24) {return new Date(Date.now() + hours * 3600000).toISOString().slice(0, 16);}
export function utcValue(value: string) {return new Date(value + 'Z').toISOString();}
const basics: Grant[] = ['context:read', 'context:write', 'projects:read', 'projects:write', 'skills:read', 'memory:read', 'memory:write'].map(action => ({action, resource_kind: action.startsWith('projects:') ? 'project_directory' : action.startsWith('skills:') ? 'skill' : 'project', selector: 'none', ids: []}));
export function TupleEditor({label, value, onChange, available = basics}: {label: string; value: Grant[]; onChange: (value: Grant[]) => void; available?: Grant[]}) {
  const choices = [...new Map(available.map(g => [g.action + '|' + g.resource_kind, g])).values()];
  const change = (i: number, patch: Partial<Grant>) => onChange(value.map((g, n) => n === i ? {...g, ...patch} : g));
  return <fieldset className="tuple-editor"><legend>{label}</legend><p className="hint">Each row couples one action with one resource dimension. New rows start at NONE.</p>
    {value.length === 0 && <p className="none-label">NONE — no grants selected.</p>}
    {value.map((g, i) => <div className="tuple-row" key={i}><Select label={`${label} action ${i + 1}`} value={g.action + '|' + g.resource_kind} onChange={e => {const choice = choices.find(c => c.action + '|' + c.resource_kind === e.target.value); if (choice) change(i, {action: choice.action, resource_kind: choice.resource_kind, selector: 'none', ids: []});}}>{choices.map(c => <option value={c.action + '|' + c.resource_kind} key={c.action + '|' + c.resource_kind}>{c.action} · {c.resource_kind}</option>)}</Select>
      <Select label={`${label} selection ${i + 1}`} value={g.selector} onChange={e => change(i, {selector: e.target.value as Grant['selector'], ids: []})}><option value="none">NONE</option><option value="include">Selected IDs</option><option value="all_in_org">All in this organization</option></Select>
      {g.selector === 'include' && <IDsInput label={`${label} IDs ${i + 1}`} value={g.ids} onChange={ids => change(i, {ids})}/>}
      <Button variant="quiet" aria-label={`Remove ${label} row ${i + 1}`} onClick={() => onChange(value.filter((_, n) => n !== i))}>Remove</Button></div>)}
    <Button variant="secondary" disabled={!choices.length || value.length >= 100} onClick={() => {const g = choices[0]; if (g) onChange([...value, {action: g.action, resource_kind: g.resource_kind, selector: 'none', ids: []}]);}}>Add {label.toLowerCase()} row</Button>
  </fieldset>;
}
export function PolicyForm({mode, target = '', onClose}: {mode: 'grant' | 'invite' | 'request'; target?: string; onClose: () => void}) {
  const {auth, data, base} = useConsole(); const context = useAuthoring(); const command = useCommand();
  const [parentId, setParentId] = useState(''); const [subject, setSubject] = useState(target); const [role, setRole] = useState('contributor');
  const [email, setEmail] = useState(''); const [name, setName] = useState(''); const [expiry, setExpiry] = useState(utcInput()); const [teamId, setTeamId] = useState('');
  const [tuples, setTuples] = useState<Grant[]>([]); const [ceiling, setCeiling] = useState<Grant[]>([]); const [targets, setTargets] = useState<Reviewed['targets']>({mode: 'none', ids: []}); const [roles, setRoles] = useState<string[]>([]); const [depth, setDepth] = useState(0);
  const [review, setReview] = useState<Reviewed | null>(null); const [subjectRevision, setSubjectRevision] = useState<number | null>(null); useDirty(true);
  const parent = context.data?.parents.find(p => p._id === parentId); const requestParent = context.data?.request_parents.find(p => p.id === parentId);
  const available = parent?.ceiling;
  function policy(): Reviewed {
    const chosenTeam = mode === 'request' ? requestParent?.team_id ?? null : teamId || parent?.team_id || null;
    return {role: mode === 'request' ? data.me.membership.role : role, parent_id: parentId,
      expected_parent_generation: parent?.generation ?? requestParent?.generation ?? 0, tuples, ceiling,
      targets, allowed_roles: roles, remaining_depth: depth, expires_at: utcValue(expiry), team_id: chosenTeam,
      expected_team_generation: chosenTeam ? context.data?.teams.find(t => t.team_id === chosenTeam)?.generation ?? 0 : null};
  }
  async function preview(event: FormEvent) {
    event.preventDefault(); setReview(null);
    await command.run(async () => {const proposed = policy();
      if (mode === 'grant') {const validated = await auth.act(base + '/management/policy-preview', previewSchema, {...proposed, subject_id: subject}); setSubjectRevision(validated.membership_revision);}
      setReview(proposed);
    }, false);
  }
  async function submit() {
    if (!review || !context.data) return;
    await command.run(async () => {
      if (mode === 'grant') await auth.act(base + '/delegations', voidSchema, {...review, subject_id: subject, expected_subject_revision: subjectRevision});
      if (mode === 'invite') await auth.act(base + '/invitations', voidSchema, {email, display_name: name, policy: review, expires_at: review.expires_at});
      if (mode === 'request') await auth.act(base + '/access-requests', voidSchema, {policy: review, expires_at: review.expires_at, expected_policy_revision: context.data.policy_revision});
      onClose();
    });
  }
  return <><StepUp/><p className="hint">Human roles label responsibilities. Personal use and onward delegation are separate, bounded policies.</p>{context.isError && <Notice tone="danger">Authoring context is unavailable.</Notice>}{command.error && <Notice tone="danger">{command.error}</Notice>}
    <form onSubmit={e => {void preview(e);}} className="form-stack">
      {mode === 'invite' ? <><Input label="Recipient email" type="email" required value={email} onChange={e => setEmail(e.target.value)}/><Input label="Display name" required maxLength={128} value={name} onChange={e => setName(e.target.value)}/></> : mode === 'grant' && <Input label="Member ID" required value={subject} onChange={e => setSubject(e.target.value)}/>}
      <Select label="Reviewed parent authority" required value={parentId} onChange={e => {setParentId(e.target.value); const chosen = context.data?.parents.find(p => p._id === e.target.value); if (chosen) {if (!chosen.allowed_roles.includes(role)) setRole(context.data?.roles.find(r => chosen.allowed_roles.includes(r)) ?? 'contributor'); setExpiry(new Date(Math.min(Date.now() + 86400000, Date.parse(chosen.expires_at))).toISOString().slice(0, 16));} setTuples([]); setCeiling([]); setTargets({mode: 'none', ids: []}); setRoles([]); setDepth(0); setTeamId('');}}><option value="">Select current authority</option>{mode === 'request' ? context.data?.request_parents.map(p => <option key={p.id} value={p.id}>{p.issuer_id} · {p.team_id ?? 'organization'} · {p.id}</option>) : context.data?.parents.filter(p => p.remaining_depth > 0).map(p => <option key={p._id} value={p._id}>{p.team_id ?? 'Organization'} · generation {p.generation} · {p._id}</option>)}</Select>
      {mode !== 'request' && <><Select label="Human role" value={role} onChange={e => setRole(e.target.value)}>{(parent ? context.data?.roles.filter(r => parent.allowed_roles.includes(r)) : context.data?.roles)?.map(r => <option key={r} value={r}>{r.replaceAll('_', ' ')}</option>)}</Select><Select label="Team scope" value={teamId || parent?.team_id || ''} disabled={Boolean(parent?.team_id)} onChange={e => setTeamId(e.target.value)}><option value="">No team scope</option>{context.data?.teams.filter(t => t.status === 'active').map(t => <option key={t.team_id} value={t.team_id}>{t.name}</option>)}</Select></>}
      <Input label="Expiry (UTC)" type="datetime-local" required value={expiry} onChange={e => setExpiry(e.target.value)}/>
      <TupleEditor label="Personal use" value={tuples} onChange={setTuples} {...(available ? {available} : {})}/>
      {mode !== 'request' && <details><summary>Onward delegation envelope (defaults to NONE)</summary><TupleEditor label="Delegation ceiling" value={ceiling} onChange={setCeiling} available={available ?? []}/><Select label="Delegation target selection" value={targets.mode} onChange={e => setTargets({mode: e.target.value as Reviewed['targets']['mode'], ids: []})}><option value="none">NONE</option><option value="include">Selected members</option><option value="all_in_org">All in this organization</option></Select>{targets.mode === 'include' && <IDsInput key={parentId} label="Delegation target IDs" value={targets.ids} onChange={ids => setTargets({...targets, ids})}/>}<IDsInput key={parentId + '-roles'} label="Allowed onward human roles (empty means NONE)" value={roles} onChange={setRoles}/><Input label="Remaining delegation depth" type="number" min={0} max={Math.min(2, (parent?.remaining_depth ?? 1) - 1)} value={depth} onChange={e => setDepth(Number(e.target.value))}/></details>}
      <div className="actions"><Button variant="secondary" onClick={onClose}>Cancel</Button><Button type="submit" busy={command.busy} disabled={!parentId || data.security.step_up_required || (mode === 'grant' && subject === data.me.user_id)}>Review policy</Button></div>
    </form>
    <Modal title="Review bounded policy" description={mode === 'grant' ? 'Validated against the current parent and resources. Submission rechecks authority; a preview does not reserve it.' : 'Review the exact proposal. The server validates current authority and resources on submission and again at acceptance or decision.'} open={review !== null} onOpenChange={open => {if (!open) setReview(null);}}>{review && <><p><strong>{mode === 'invite' ? email : mode === 'request' ? data.me.display_name : subject}</strong> · {review.role.replaceAll('_', ' ')} · expires {review.expires_at}</p><GrantTable grants={review.tuples}/><h3>Onward delegation ceiling</h3><GrantTable grants={review.ceiling}/><p>Targets: {review.targets.mode === 'include' ? review.targets.ids.join(', ') : review.targets.mode.toUpperCase()} · Depth: {review.remaining_depth} · Roles: {review.allowed_roles.join(', ') || 'NONE'}</p><Notice>Existing assignments stay in place. Narrowing or revocation is a separate reviewed action. Expiry or parent revocation removes future authority.</Notice>{command.error && <Notice tone="danger">{command.error}</Notice>}<Button busy={command.busy} onClick={() => {void submit();}}>Confirm {mode === 'invite' ? 'invitation' : mode === 'request' ? 'access request' : 'assignment'}</Button></>}</Modal>
  </>;
}
