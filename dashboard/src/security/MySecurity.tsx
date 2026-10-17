import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { namespace, StaleContext } from '../api/client';
import { enrollmentSchema, sessionResultSchema, sessionsSchema, voidSchema } from '../api/contracts';
import { errorText, useAuth } from '../auth/context';
import { Button, Empty, ErrorState, exactTime, Icon, Input, Modal, Notice, Panel, Select, Skeleton, Status, Table } from '../components/ui';

export function MySecurity() {
  const auth = useAuth();
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [enrollment, setEnrollment] = useState<{seed: string; provisioning_uri: string} | null>(null);
  const [revoke, setRevoke] = useState<string | null>(null); const [remove, setRemove] = useState(false);
  const [linkOrg, setLinkOrg] = useState('');
  const data = auth.state.status === 'active' ? auth.state.data : null;
  const sessions = useQuery({
    queryKey: data ? [...namespace(data, auth.generation), 'human-browser-sessions'] : ['inactive'],
    enabled: data !== null,
    queryFn: ({signal}) => auth.read('/me/sessions', sessionsSchema, signal),
  });
  if (!data) return null;
  const security = data.security; const currentOrg = data.organizations.find(org => org.org_id === data.me.policy_context.org_id);
  const providers = data.organizations.filter(org => org.auth_policy !== 'local_only');
  const inputChanged = () => auth.setDirty(true);
  async function task(work: () => Promise<void>, keepDirty = false) {
    setBusy(true); setError(''); setMessage('');
    try {await work(); auth.setDirty(keepDirty);}
    catch (failure) {if (!(failure instanceof StaleContext)) setError(errorText(failure));}
    finally {setBusy(false);}
  }
  const factor = async (event: FormEvent<HTMLFormElement>, path: string, recovery = false) => {
    event.preventDefault(); const form = event.currentTarget; const values = new FormData(form); const code = String(values.get('code') ?? ''); form.reset();
    await task(async () => {await auth.accept(await auth.act(path, sessionResultSchema, {code, recovery: recovery && values.has('recovery')}));});
  };
  return <>
    <header className="page-heading"><span className="eyebrow">Your account</span><h1>My security</h1><p>Manage your sign-in methods and human browser sessions.</p></header>
    {error && <Notice tone="danger">{error}</Notice>}{message && <Notice tone="success">{message}</Notice>}
    <div className="security-grid">
      <Panel title="Sign-in security" description="Your current organization’s policy is checked on every request.">
        <div className="security-summary"><Icon name="shield"/><div><strong>{data.me.auth_method === 'oidc' ? 'Organization SSO' : 'Local account'}</strong><p>{security.step_up_required ? 'Recent verification needed for sensitive changes' : 'Recently verified'}</p></div><Status tone={security.step_up_required ? 'warning' : 'success'}>{security.assurance === 'mfa' ? 'MFA verified' : 'Signed in'}</Status></div>
        {currentOrg?.auth_policy === 'sso_required' && <Notice>Your organization requires its approved SSO. Local account changes do not change that entry policy.</Notice>}
        <dl className="detail-list"><div><dt>Organization</dt><dd>{currentOrg?.name ?? data.me.policy_context.org_id}</dd></div><div><dt>Session expiry</dt><dd>{exactTime(security.session.expires_at)}</dd></div></dl>
        {security.step_up_required && <div className="inset"><h3>Verify before making changes</h3>{data.me.auth_method === 'oidc' && <Button busy={busy} onClick={() => {void task(() => auth.provider(data.me.policy_context.org_id, 'reauth'));}}>Reauthenticate with provider</Button>}{data.me.auth_method === 'oidc' && security.mfa_enrolled && <p className="hint">Your enrolled local factor can verify this security action. It does not change your organization’s SSO entry policy.</p>}{security.mfa_enrolled ? <form aria-label="Step up authentication" onSubmit={event => {void factor(event, '/auth/step-up', true);}} onChange={inputChanged}><Input label="Authentication or recovery code" name="code" autoComplete="one-time-code" required minLength={6} maxLength={128}/><label className="checkbox"><input type="checkbox" name="recovery"/>Use a recovery code</label><Button type="submit" busy={busy}>Verify identity</Button></form> : <p>{data.me.auth_method === 'local' ? 'Enroll an authenticator below to verify sensitive changes.' : 'Your approved provider must supply trusted MFA assurance for sensitive changes.'}</p>}</div>}
      </Panel>
      <Panel title="Authenticator & recovery" description="Local TOTP factors and single-use recovery codes.">
        <div className="card-topline"><Status tone={security.mfa_enrolled ? 'success' : 'warning'}>{security.mfa_enrolled ? 'Authenticator enrolled' : 'Enrollment needed'}</Status>{security.mfa_enrolled && <span className="muted small">{security.recovery_codes_remaining} unused recovery codes</span>}</div>
        {enrollment ? <form aria-label="Confirm authenticator" onSubmit={event => {void factor(event, '/auth/mfa/confirm');}} onChange={inputChanged}>
          <p>Add this account to your authenticator. The setup key stays on this screen only.</p>
          <div className="secret-setup"><span className="small muted">Setup key</span><code data-testid="setup-seed">{enrollment.seed}</code><Button variant="secondary" onClick={() => {void navigator.clipboard.writeText(enrollment.provisioning_uri);}}>Copy setup URI</Button></div>
          <Input label="New authenticator code" name="code" inputMode="numeric" autoComplete="one-time-code" minLength={6} maxLength={6} required/>
          <div className="actions"><Button type="submit" busy={busy}>Confirm authenticator</Button><Button variant="quiet" onClick={() => {setEnrollment(null); auth.setDirty(false);}}>Cancel setup</Button></div>
        </form> : !security.mfa_enrolled ? <><p>Protect your account with a time-based authenticator. Recovery codes are issued after confirmation.</p><Button busy={busy} disabled={!security.local_enrollment_allowed} onClick={() => {void task(async () => {setEnrollment(await auth.act('/auth/mfa/enroll', enrollmentSchema, {}));}, true);}}>Set up authenticator</Button>{!security.local_enrollment_allowed && <p className="hint">A recent permitted local sign-in is required to enroll a local factor. SSO factors are managed by your provider.</p>}</> : <>
          <form aria-label="Replace recovery codes" onSubmit={event => {void factor(event, '/auth/mfa/recovery');}} onChange={inputChanged}><Input label="Fresh authenticator code" name="code" inputMode="numeric" autoComplete="one-time-code" minLength={6} maxLength={6} required/><Button variant="secondary" type="submit" busy={busy} disabled={security.step_up_required}>Replace recovery codes</Button><p className="hint">This invalidates previous codes and other browser sessions. Use a fresh code.</p></form>
          <Button variant="quiet" className="danger-text" disabled={security.step_up_required || busy} onClick={() => setRemove(true)}>Remove local authenticator</Button>
        </>}
      </Panel>
      <Panel title="Local password" description="Passwords are never shown or recoverable.">
        {security.local_password_available ? <form aria-label="Change local password" onChange={inputChanged} onSubmit={event => {
          event.preventDefault(); const form = event.currentTarget; const values = new FormData(form);
          const body = {old_password: String(values.get('old_password') ?? ''), new_password: String(values.get('new_password') ?? '')}; form.reset();
          void task(async () => {await auth.act('/auth/password/change', voidSchema, body); auth.expired('Password changed. Sign in again with MFA.');});
        }}><Input label="Current password" name="old_password" type="password" autoComplete="current-password" required maxLength={1024}/><Input label="New password" name="new_password" type="password" autoComplete="new-password" required minLength={12} maxLength={1024} hint="At least 12 characters. Changing it ends existing browser sessions."/><Button type="submit" busy={busy} disabled={security.step_up_required}>Change password</Button></form> : <Empty title="No local password">Your verified provider manages your sign-in credentials.</Empty>}
      </Panel>
      <Panel title="Verified login identities" description="Linking proves both identities and ends existing browser sessions.">
        <ul className="identity-list">{security.identities.map((identity, index) => <li key={`${identity.kind}-${index}`}><Icon name={identity.kind === 'oidc' ? 'link' : 'key'}/><div><strong>{identity.kind === 'local' ? 'Local account' : 'SSO identity'}</strong>{identity.issuer && <p className="break-word">{identity.issuer}</p>}<span className="small muted">Verified {exactTime(identity.verified_at)}</span></div><Status tone="success">Verified</Status></li>)}</ul>
        {providers.length > 0 ? <><Select label="Organization provider to link" value={linkOrg} onChange={event => {setLinkOrg(event.target.value); inputChanged();}}><option value="">Choose an organization</option>{providers.map(org => <option key={org.org_id} value={org.org_id}>{org.name}</option>)}</Select><Button variant="secondary" disabled={!linkOrg || security.step_up_required || busy} onClick={() => {void task(() => auth.provider(linkOrg, 'link'));}}>Link a verified identity</Button><p className="hint">Matching email addresses never link accounts automatically.</p></> : <p className="hint">None of your organizations currently offers an SSO provider.</p>}
      </Panel>
    </div>
    <Panel title="Human browser sessions" description="These are your console sign-ins, separate from agent sessions.">
      {sessions.isPending ? <Skeleton label="Loading browser sessions"/> : sessions.isError ? <ErrorState onRetry={() => {void sessions.refetch();}}>Browser sessions could not be loaded.</ErrorState> : <Table label="Your human browser sessions" headings={['Browser session', 'Organization', 'Last recorded use', 'Status', 'Action']}>
        {sessions.data.sessions.map(session => {const ended = session.revoked || Date.parse(session.expires_at) <= Date.now() || Date.parse(session.idle_expires_at) <= Date.now(); return <tr key={session.public_id}><td><strong>{session.public_id === data.me.session_id ? 'This browser' : 'Browser session'}</strong><span className="cell-note">Started {exactTime(session.created_at)}</span></td><td>{data.organizations.find(org => org.org_id === session.org_id)?.name ?? 'Organization session'}</td><td>{exactTime(session.last_seen)}</td><td><Status tone={ended ? 'neutral' : 'success'}>{session.revoked ? 'Revoked' : ended ? 'Expired' : 'Active'}</Status></td><td>{!ended && <Button variant="secondary" disabled={busy || security.step_up_required} onClick={() => setRevoke(session.public_id)} aria-label={session.public_id === data.me.session_id ? 'Revoke this browser session' : 'Revoke browser session'}>Revoke</Button>}</td></tr>;})}
      </Table>}
    </Panel>
    <Modal title="Revoke browser session?" description="The selected human browser session will lose future console access. Already completed work stays recorded." open={revoke !== null} onOpenChange={open => {if (!open) setRevoke(null);}}><div className="actions"><Button variant="secondary" onClick={() => setRevoke(null)}>Cancel</Button><Button variant="danger" busy={busy} onClick={() => {const target = revoke; setRevoke(null); if (target) void task(async () => {await auth.act('/me/sessions/' + encodeURIComponent(target) + '/revoke', voidSchema, {}); if (target === data.me.session_id) auth.expired('This browser session was revoked.'); else {await sessions.refetch(); setMessage('Browser session revoked.');}});}}>Revoke session</Button></div></Modal>
    <Modal title="Remove local authenticator?" description="Local recovery codes will stop working and existing browser sessions will end. This does not change your provider’s MFA or your organization’s sign-in policy." open={remove} onOpenChange={setRemove}><div className="actions"><Button variant="secondary" onClick={() => setRemove(false)}>Keep authenticator</Button><Button variant="danger" busy={busy} onClick={() => {setRemove(false); void task(async () => {await auth.act('/auth/mfa/remove', voidSchema, {}); auth.expired('Local authenticator removed. Sign in again.');});}}>Remove authenticator</Button></div></Modal>
  </>;
}
