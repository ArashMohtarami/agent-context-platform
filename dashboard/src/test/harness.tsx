import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import { App } from '../main';
import { AuthProvider } from '../auth/context';
import type { Bootstrap } from '../api/contracts';

export function fixture(org = 'org-a'): Bootstrap {
  const now = new Date().toISOString(); const later = new Date(Date.now() + 3600000).toISOString();
  return {me: {user_id: 'person', display_name: 'Fictional Alice', session_id: 'session-' + org,
    membership: {org_id: org, display_name: 'Fictional Alice', role: 'contributor', status: 'active'}, auth_method: 'local', assurance: 'mfa', policy_revision: 2,
    policy_context: {org_id: org, session_id: 'session-' + org, membership_revision: 1, policy_version: 3, context_id: 'context-' + org}},
  organizations: [{org_id: 'org-a', name: 'Fictional Alpha', role: 'contributor', auth_policy: 'sso_preferred'}, {org_id: 'org-b', name: 'Fictional Beta', role: 'reviewer', auth_policy: 'sso_required'}], grants: [],
  security: {auth_method: 'local', assurance: 'mfa', mfa_enrolled: true, local_password_available: true, local_enrollment_allowed: true, step_up_required: false, recovery_codes_remaining: 8,
    identities: [{kind: 'local', issuer: null, verified_at: now}], session: {public_id: 'session-' + org, created_at: now, expires_at: later, idle_expires_at: later}}};
}
export type Handler = (path: string, init: RequestInit) => Promise<Response> | Response | undefined;
export function fakeApi(initial: Bootstrap | null, handler?: Handler) {
  let active = initial;
  const requests: {path: string; init: RequestInit}[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const path = String(input).replace('/api/v6', ''); requests.push({path, init});
    const custom = handler?.(path, init); if (custom) return await custom;
    if (path === '/auth/csrf') return reply({csrf_token: 'test-csrf'});
    if (path === '/me/console') return active ? reply(active) : reply({detail: 'authentication was not accepted'}, 401);
    if (path === '/me/effective-access') return reply({org_id: active?.me.membership.org_id, subject_id: 'person', policy_revision: 2, tuples: [], role_has_corpus_bypass: false});
    if (path === '/me/sessions') return reply({sessions: active ? [{...active.security.session, org_id: active.me.membership.org_id, last_seen: new Date().toISOString(), revoked: false}] : []});
    if (path === '/auth/logout') {active = null; return new Response(null, {status: 204});}
    throw new Error('Unexpected API path: ' + path);
  });
  vi.stubGlobal('fetch', fetcher);
  return {requests, fetcher, setActive: (data: Bootstrap | null) => {active = data;}};
}
export function reply(value: unknown, status = 200) {return new Response(JSON.stringify(value), {status, headers: {'Content-Type': 'application/json'}});}
export function mount(path: string) {
  const queries = new QueryClient({defaultOptions: {queries: {retry: false, gcTime: 0}, mutations: {retry: false}}});
  return {...render(<QueryClientProvider client={queries}><MemoryRouter initialEntries={[path]}><AuthProvider><App/></AuthProvider></MemoryRouter></QueryClientProvider>), queries};
}
