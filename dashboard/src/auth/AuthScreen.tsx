import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link, Navigate, useLocation } from 'react-router-dom';
import { Button, Icon, Input, Notice, Skeleton } from '../components/ui';
import { loginSchema, messageSchema, sessionResultSchema, voidSchema } from '../api/contracts';
import { errorText, SessionUnavailable, useAuth } from './context';
import mark from '../assets/mark.svg';

type Mode = 'login' | 'reset-request' | 'reset' | 'invite' | 'verify';
const definitions: Record<Mode, {title: string; description: string; action: string}> = {
  login: {title: 'Your context, connected.', description: 'Sign in to your organization workspace.', action: 'Sign in'},
  'reset-request': {title: 'Reset your password', description: 'We’ll send instructions if your account is eligible.', action: 'Request reset'},
  reset: {title: 'Choose a new password', description: 'Use the reset code from your verification message. Your MFA settings stay in place.', action: 'Reset password'},
  invite: {title: 'Join your workspace', description: 'Enrollment is invitation-only. Enter the invitation code you received.', action: 'Accept invitation'},
  verify: {title: 'Verify your account', description: 'Enter the verification code from your message in this browser.', action: 'Verify account'},
};
export function AuthScreen({mode = 'login'}: {mode?: Mode}) {
  const auth = useAuth(); const location = useLocation();
  const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [challenge, setChallenge] = useState<string | null>(null);
  const [recovery, setRecovery] = useState(false);
  const [org, setOrg] = useState('');
  const definition = definitions[mode];
  if (auth.state.status === 'active') return <Navigate to={`/o/${encodeURIComponent(auth.state.data.me.policy_context.org_id)}/workspace`} replace/>;
  if (auth.state.status === 'loading' || auth.state.status === 'switching') return <main id="main-content" className="loading-screen"><Skeleton label="Checking your secure session"/></main>;
  if (auth.state.status === 'unavailable') return <SessionUnavailable/>;
  const params = new URLSearchParams(location.search);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(''); setNotice(''); setBusy(true);
    const form = event.currentTarget; const values = new FormData(form);
    const text = (key: string) => String(values.get(key) ?? '');
    try {
      if (mode === 'login') {
        if (challenge) {
          const result = await auth.act('/auth/local/complete', sessionResultSchema, {token: challenge, code: text('code'), recovery});
          setChallenge(null); await auth.accept(result);
        } else {
          const result = await auth.act('/auth/local/login', loginSchema, {email: text('email'), password: text('password'), org_id: org});
          form.reset();
          if (result.mfa_required) setChallenge(result.challenge);
          else await auth.accept(await auth.act('/auth/local/complete', sessionResultSchema, {token: result.challenge, code: '', recovery: false}));
        }
      } else if (mode === 'reset-request') {
        await auth.act('/auth/password-reset/request', messageSchema, {email: text('email')});
        form.reset(); setNotice('If eligible, a reset message will be delivered.');
      } else if (mode === 'verify') {
        await auth.act('/auth/local/verify', voidSchema, {token: text('token')});
        form.reset(); setNotice('Verification accepted. You can now sign in.');
      } else if (mode === 'reset') {
        await auth.act('/auth/password-reset/consume', voidSchema, {token: text('token'), password: text('password'), code: ''});
        form.reset(); setNotice('Password reset accepted. Sign in with your new password and existing MFA.');
      } else {
        await auth.act('/auth/invitations/accept', messageSchema, {token: text('token'), password: text('password'), code: text('code')});
        form.reset(); setNotice('Continue with the verification message if the invitation was accepted.');
      }
    } catch (failure) {setError(errorText(failure)); if (mode === 'login' && !challenge) form.reset();}
    finally {setBusy(false);}
  }
  return <div className="auth-layout">
    <aside className="auth-brand"><Link to="/sign-in" className="brand"><img src={mark} alt=""/><span>Agent Context<span className="brand-caption">Organization console</span></span></Link>
      <div className="auth-story"><span className="eyebrow">Knowledge with clear boundaries</span><p className="brand-statement">A shared workspace.<br/>Access you can trust.</p><p>Connect to the context your organization has shared with you. Your identity, permissions and security stay explicit.</p><div className="story-rule"/><p className="small">An invitation starts your account.<br/>Your organization controls access.</p></div>
      <span className="auth-footnote"><Icon name="shield"/>Secure, session-based access</span>
    </aside>
    <main id="main-content" className="auth-main"><div className="auth-card">
      <Link to="/sign-in" className="mobile-brand"><img src={mark} alt=""/>Agent Context</Link>
      <span className="eyebrow">Organization workspace</span>
      <h1>{challenge ? 'One more security check' : definition.title}</h1>
      <p className="lede">{challenge ? 'Use your authenticator or a single-use recovery code.' : definition.description}</p>
      {auth.state.status === 'anonymous' && auth.state.message && <Notice tone="warning">{auth.state.message}</Notice>}
      {params.has('linked') && <Notice tone="success">Login identity linked. Sign in again to verify your current access.</Notice>}
      {params.has('logged_out') && <Notice tone="success">You signed out of the provider and this console.</Notice>}
      {error && <Notice tone="danger">{error}</Notice>}{notice && <Notice tone="success">{notice}</Notice>}
      <form onSubmit={event => {void submit(event);}} aria-label={challenge ? 'MFA sign-in' : definition.title}>
        {mode === 'login' && !challenge && <>
          <Input label="Organization ID" name="org_id" value={org} onChange={event => setOrg(event.target.value)} required autoComplete="organization" maxLength={128} hint="Use the organization ID from your invitation."/>
          <Input label="Email address" name="email" type="email" autoComplete="username" required maxLength={254}/>
          <Input label="Password" name="password" type="password" autoComplete="current-password" required maxLength={1024}/>
        </>}
        {mode === 'reset-request' && <Input label="Email address" name="email" type="email" autoComplete="username" required maxLength={254}/>}
        {['invite', 'verify', 'reset'].includes(mode) && <Input label={mode === 'invite' ? 'Invitation code' : mode === 'reset' ? 'Reset code' : 'Verification code'} name="token" type="password" autoComplete="off" required minLength={32} maxLength={128} hint="Paste the code from your message. Codes expire and work once."/>}
        {['invite', 'reset'].includes(mode) && <Input label={mode === 'reset' ? 'New password' : 'Account password'} name="password" type="password" autoComplete="new-password" required minLength={12} maxLength={1024} hint="Use at least 12 characters. For an existing account, accepting an invite requires its current password."/>}
        {challenge && <>
          <Input label={recovery ? 'Recovery code' : 'Authentication code'} name="code" type="text" autoComplete="one-time-code" inputMode={recovery ? 'text' : 'numeric'} minLength={6} maxLength={128} required autoFocus/>
          <label className="checkbox"><input type="checkbox" checked={recovery} onChange={event => setRecovery(event.target.checked)}/>Use a recovery code</label>
        </>}
        {mode === 'invite' && <Input label="Authentication code (existing accounts with MFA)" name="code" autoComplete="one-time-code" inputMode="numeric" maxLength={128}/>}
        <Button type="submit" busy={busy} className="full-width">{challenge ? 'Verify and sign in' : definition.action}<Icon name="arrow"/></Button>
      </form>
      {mode === 'login' && !challenge && <>
        <div className="divider"><span>or</span></div>
        <Button variant="secondary" busy={busy} className="full-width" disabled={!org} onClick={() => {setBusy(true); void auth.provider(org, 'login').catch(() => undefined).finally(() => setBusy(false));}}>Continue with SSO</Button>
        <p className="hint">Your organization’s approved provider verifies your identity. Local sign-in cannot bypass its SSO policy.</p>
        <nav className="auth-links" aria-label="Account help"><Link to="/reset-request">Forgot password?</Link><Link to="/invitation">Accept invitation</Link><Link to="/verify">Verify account</Link></nav>
      </>}
      {challenge && <Button variant="quiet" onClick={() => {setChallenge(null); setError('');}}>Start sign-in again</Button>}
      {mode !== 'login' && <nav className="auth-links" aria-label="Sign-in help"><Link to="/sign-in">Back to sign in</Link>{mode === 'reset-request' && <Link to="/reset">I have a reset code</Link>}{mode === 'invite' && <Link to="/verify">I have a verification code</Link>}</nav>}
    </div><p className="auth-bottom">Private workspace access · Agent Context Platform</p></main>
  </div>;
}
