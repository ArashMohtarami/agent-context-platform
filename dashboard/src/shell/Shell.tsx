import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Link, Navigate, NavLink, Outlet, useLocation, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { effectiveSchema } from '../api/contracts';
import { namespace } from '../api/client';
import { errorText, SessionUnavailable, useAuth } from '../auth/context';
import { Button, Empty, ErrorState, Icon, Modal, Notice, Panel, Select, Skeleton, Status, Table } from '../components/ui';
import type { Organization } from '../api/contracts';
import mark from '../assets/mark.svg';

export function ThemeButton() {
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme ? document.documentElement.dataset.theme === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches);
  useEffect(() => {document.documentElement.dataset.theme = dark ? 'dark' : 'light';}, [dark]);
  return <Button variant="quiet" aria-label={dark ? 'Use light theme' : 'Use dark theme'} onClick={() => setDark(value => !value)}><Icon name={dark ? 'sun' : 'moon'}/><span className="theme-label">{dark ? 'Light' : 'Dark'}</span></Button>;
}
export function Protected() {
  const auth = useAuth(); const params = useParams();
  if (auth.state.status === 'loading' || auth.state.status === 'switching') return <main id="main-content" className="loading-screen"><img src={mark} alt="Agent Context"/><Skeleton label={auth.state.status === 'switching' ? 'Changing organization. Previous context cleared.' : 'Checking your session'}/><p>{auth.state.status === 'switching' ? 'Changing organization…' : 'Checking your session…'}</p></main>;
  if (auth.state.status === 'unavailable') return <SessionUnavailable/>;
  if (auth.state.status !== 'active') return <Navigate to="/sign-in" replace/>;
  const orgId = auth.state.data.me.policy_context.org_id;
  if (params.orgId !== orgId) return <Navigate to={`/o/${encodeURIComponent(orgId)}/workspace`} replace/>;
  return <Shell key={namespace(auth.state.data, auth.generation).join(':')}/>;
}
function Shell() {
  const auth = useAuth(); const location = useLocation();
  const [drawer, setDrawer] = useState(false); const [target, setTarget] = useState<Organization | null>(null);
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  if (auth.state.status !== 'active') return null;
  const {me, organizations} = auth.state.data;
  const org = organizations.find(item => item.org_id === me.policy_context.org_id);
  const base = `/o/${encodeURIComponent(me.policy_context.org_id)}`;
  const links = [{to: base + '/workspace', label: 'My workspace', icon: 'home' as const}, {to: base + '/my-access', label: 'My access', icon: 'key' as const}, {to: base + '/security', label: 'My security', icon: 'shield' as const}];
  const pageName = links.find(link => location.pathname === link.to)?.label ?? 'Workspace';
  const navigation = <nav className="side-nav" aria-label="Organization navigation"><span className="nav-section">Workspace</span>{links.map(link => <NavLink key={link.to} to={link.to} onClick={() => setDrawer(false)}><Icon name={link.icon}/>{link.label}</NavLink>)}<div className="nav-foot"><Icon name="users"/><strong>Organization context</strong><p>Installation operations use the separate operator CLI.</p></div></nav>;
  async function change(selected: Organization) {
    setBusy(true); setError('');
    try {await auth.switchOrganization(selected);}
    catch (failure) {setError(errorText(failure));}
    finally {setBusy(false);}
  }
  const selectOrganization = (id: string) => {
    const selected = organizations.find(item => item.org_id === id);
    if (!selected || selected.org_id === me.policy_context.org_id) return;
    if (auth.dirty) setTarget(selected); else void change(selected);
  };
  return <div className="shell private-surface">
    <header className="topbar"><div className="topbar-start"><Button variant="quiet" className="mobile-menu" aria-label="Open navigation" onClick={() => setDrawer(true)}><Icon name="menu"/></Button><Link className="brand" aria-label="Agent Context organization workspace" to={base + '/workspace'}><img src={mark} alt=""/><span>Agent Context<span className="brand-caption">Organization console</span></span></Link><span className="header-divider"/><span className="breadcrumb">{pageName}</span></div>
      <div className="topbar-actions"><Select label="Active organization" aria-label="Active organization" value={me.policy_context.org_id} disabled={busy} onChange={event => selectOrganization(event.target.value)}>{organizations.map(item => <option value={item.org_id} key={item.org_id}>{item.name}</option>)}</Select><ThemeButton/><Button variant="quiet" onClick={() => {void auth.logout();}} aria-label="Sign out"><Icon name="exit"/><span className="theme-label">Sign out</span></Button></div>
    </header>
    <aside className="sidebar">{navigation}</aside>
    <div className="main-area"><div className="context-strip"><span><span className="context-dot"/>{org?.name ?? me.policy_context.org_id}</span><span className="context-user">{me.display_name}<span className="role-label">{me.membership.role.replaceAll('_', ' ')}</span></span></div><main id="main-content" className="page-content" tabIndex={-1}>{auth.state.message && <Notice tone="danger">{auth.state.message}</Notice>}{error && <Notice tone="danger">{error}</Notice>}<Outlet/></main><footer className="shell-footer">Agent Context Platform<span>Organization workspace · Access verified by the server</span></footer></div>
    <Modal title="Organization navigation" description="Navigate your current organization workspace." open={drawer} onOpenChange={setDrawer} drawer>{navigation}</Modal>
    <Modal title="Discard unsaved changes?" description={`Finish or save your current form in ${org?.name ?? 'this organization'}, or discard its sensitive inputs before switching to ${target?.name ?? 'another organization'}. Drafts and selections will be cleared.`} open={target !== null} onOpenChange={open => {if (!open) setTarget(null);}}><div className="actions"><Button variant="secondary" onClick={() => setTarget(null)}>Keep editing</Button><Button variant="danger" onClick={() => {const selected = target; setTarget(null); if (selected) void change(selected);}}>Discard and switch</Button></div></Modal>
  </div>;
}
export function Workspace() {
  const auth = useAuth(); if (auth.state.status !== 'active') return null;
  const data = auth.state.data; const org = data.organizations.find(item => item.org_id === data.me.policy_context.org_id);
  const base = `/o/${encodeURIComponent(data.me.policy_context.org_id)}`;
  const management = data.grants.some(grant => grant.selector !== 'none' && ['members:read', 'teams:read', 'activity:read:team', 'activity:read:org'].includes(grant.action));
  return <>
    <header className="page-heading"><span className="eyebrow">{management ? 'Your organization workspace' : 'Your personal workspace'}</span><h1>Welcome, {data.me.display_name}</h1><p>Your identity and current access in {org?.name ?? data.me.policy_context.org_id}.</p></header>
    <div className="welcome-panel"><div className="welcome-icon"><Icon name="shield"/></div><div><h2>A clear place to start.</h2><p>Review your access and keep your sign-in secure. Organization permissions are explicit and can change over time.</p></div><Status tone="success">Verified membership</Status></div>
    <div className="overview-grid"><Panel title="Organization context"><dl className="detail-list"><div><dt>Workspace</dt><dd>{org?.name ?? data.me.policy_context.org_id}</dd></div><div><dt>Your role</dt><dd className="capitalize">{data.me.membership.role.replaceAll('_', ' ')}</dd></div><div><dt>Sign-in policy</dt><dd>{org?.auth_policy === 'sso_required' ? 'Approved SSO required' : org?.auth_policy === 'sso_preferred' ? 'SSO preferred' : 'Local sign-in'}</dd></div></dl></Panel>
    <Panel title="My access"><p>Read your effective action and resource boundaries. A role or team name does not grant corpus access by itself.</p><Link className="button secondary" to={base + '/my-access'}>Review my access<Icon name="arrow"/></Link></Panel>
    <Panel title="My security"><p>{data.security.mfa_enrolled ? 'Your local authenticator is enrolled.' : data.me.auth_method === 'oidc' ? 'Your provider verifies your sign-in.' : 'Set up an authenticator to protect sensitive account changes.'}</p><Link className="button secondary" to={base + '/security'}>Manage sign-in security<Icon name="arrow"/></Link></Panel></div>
    <Panel title="Built around explicit access" description="Your console uses the current organization’s verified policy."><div className="principle-grid"><div><Icon name="users"/><h3>Your organization</h3><p>Switching workspaces checks membership and sign-in policy, then clears the previous context.</p></div><div><Icon name="key"/><h3>Your resource boundaries</h3><p>Read, write and management permissions stay coupled to their authorized resources.</p></div><div><Icon name="shield"/><h3>Your security</h3><p>Manage human browser sessions and verified login identities from your own account.</p></div></div></Panel>
  </>;
}
export function MyAccess() {
  const auth = useAuth(); const data = auth.state.status === 'active' ? auth.state.data : null;
  const access = useQuery({queryKey: data ? [...namespace(data, auth.generation), 'effective-access'] : ['inactive'], enabled: data !== null, queryFn: ({signal}) => auth.read('/me/effective-access', effectiveSchema, signal)});
  return <><header className="page-heading"><span className="eyebrow">Current organization</span><h1>My access</h1><p>Action-specific resource boundaries from the current server policy.</p></header><Panel title="Effective permissions">
    {access.isPending ? <Skeleton label="Loading effective access"/> : access.isError ? <ErrorState onRetry={() => {void access.refetch();}}>Your effective access could not be loaded. It may have changed.</ErrorState> : access.data.tuples.filter(grant => grant.selector !== 'none').length === 0 ? <Empty title="No effective resource assignments">Your membership is verified. Ask an authorized person to review any additional access you need.</Empty> : <Table label="Your effective action and resource grants" headings={['Action', 'Resource', 'Access']}>
      {access.data.tuples.map((grant, index) => <tr key={index}><td><code>{grant.action}</code></td><td>{grant.resource_kind.replaceAll('_', ' ')}</td><td>{grant.selector === 'all_in_org' ? 'All in this organization' : grant.selector === 'none' ? 'None' : grant.ids.join(', ')}</td></tr>)}
    </Table>}
    <p className="hint">This view explains current access. Every server request is authorized again.</p>
  </Panel></>;
}
export function PageFrame({children}: {children: ReactNode}) {return <main id="main-content" className="specimen-page">{children}</main>;}
