import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import type { z } from 'zod';
import { ApiError, namespace, providerNavigate, SessionApi, StaleContext } from '../api/client';
import type { Bootstrap, Organization, SessionResult } from '../api/contracts';
import { bootstrapSchema, logoutSchema, redirectSchema, sessionResultSchema, voidSchema } from '../api/contracts';
import { Button, Modal, Notice } from '../components/ui';

type State = {status: 'active'; data: Bootstrap; message?: string} | {status: 'loading' | 'switching' | 'anonymous' | 'unavailable'; message?: string};
interface Auth {
  state: State; api: SessionApi; generation: number;
  refresh: () => Promise<Bootstrap | undefined>;
  reconnect: () => Promise<void>;
  act: <T>(path: string, schema: z.ZodType<T>, body: unknown, method?: 'POST' | 'PATCH' | 'PUT') => Promise<T>;
  read: <T>(path: string, schema: z.ZodType<T>, signal?: AbortSignal) => Promise<T>;
  accept: (result: SessionResult) => Promise<void>;
  switchOrganization: (target: Organization) => Promise<void>;
  provider: (org: string, purpose: 'login' | 'link' | 'switch' | 'reauth') => Promise<void>;
  logout: () => Promise<void>;
  expired: (message?: string) => void;
  dirty: boolean; setDirty: (dirty: boolean) => void;
}
const Context = createContext<Auth | null>(null);
export function useAuth() {
  const value = useContext(Context);
  if (!value) throw new Error('Authentication context is unavailable');
  return value;
}
export function errorText(error: unknown) {
  if (error instanceof StaleContext) return 'The organization changed. Review this view again.';
  if (error instanceof ApiError) {
    if (error.csrf) return 'The security check failed. Review and submit again.';
    if (error.status === 429) return 'Too many attempts. Wait a moment before trying again.';
    if (error.status === 409) return 'Your access changed. Review the current context and try again.';
    if (error.status === 403) return 'This action is outside your current access.';
    if (error.status === 401) return 'The request was not accepted. Check your credentials or use a fresh sign-in.';
    if (error.status === 422) return 'Check the required fields and try again.';
  }
  return 'The service could not be reached. Try again when the connection is available.';
}

export function AuthProvider({children, client}: {children: ReactNode; client?: SessionApi}) {
  const [api] = useState(() => client ?? new SessionApi());
  const queries = useQueryClient(); const navigate = useNavigate(); const location = useLocation();
  const navigation = useRef(navigate); navigation.current = navigate;
  const [state, setState] = useState<State>({status: 'loading'});
  const current = useRef(state);
  const [dirty, setDirty] = useState(false);
  const [codes, setCodes] = useState<string[]>([]);
  const [targetProvider, setTargetProvider] = useState<Organization | null>(null);
  const channel = useRef<BroadcastChannel | null>(null);
  const publish = useCallback((next: State) => { current.current = next; setState(next); }, []);
  const clear = useCallback((status: 'loading' | 'switching' = 'loading') => {
    api.clear(); void queries.cancelQueries(); queries.clear();
    setDirty(false); setCodes([]); setTargetProvider(null); publish({status});
  }, [api, queries, publish]);
  const expired = useCallback((message = 'Your session ended. Sign in again to continue.') => {
    api.clear(); void queries.cancelQueries(); queries.clear(); setDirty(false); setCodes([]); setTargetProvider(null);
    publish({status: 'loading'}); navigation.current('/sign-in', {replace: true});
    const epoch = api.generation;
    void (async () => {
      try {await api.request('/auth/logout', voidSchema, {body: {}});} catch { /* An expired cookie is cleared by the CSRF endpoint. */ }
      if (api.generation !== epoch) return;
      api.forget();
      try {await api.initialize(); publish({status: 'anonymous', message});}
      catch {publish({status: 'unavailable', message: 'Reconnect to verify your session.'});}
    })();
  }, [api, queries, publish]);
  const refresh = useCallback(async () => {
    try {
      const data = await api.request('/me/console', bootstrapSchema, {bootstrap: true});
      const before = current.current;
      if (before.status === 'active' && JSON.stringify(namespace(before.data, 0)) !== JSON.stringify(namespace(data, 0))) clear();
      api.context(data); publish({status: 'active', data});
      return data;
    } catch (error) {
      if (error instanceof StaleContext) return undefined;
      if (error instanceof ApiError && error.status === 401) {
        if (current.current.status === 'active') expired();
        else publish({status: 'anonymous'});
      } else publish({status: 'unavailable', message: errorText(error)});
      return undefined;
    }
  }, [api, clear, expired, publish]);
  const initializing = useRef<Promise<void> | null>(null);
  const initialize = useCallback(() => {
    if (initializing.current) return initializing.current;
    const work = (async () => {
      try {await api.initialize(); await refresh();}
      catch (error) {if (!(error instanceof StaleContext)) publish({status: 'unavailable', message: errorText(error)});}
      finally {initializing.current = null;}
    })();
    initializing.current = work;
    return work;
  }, [api, refresh, publish]);
  const reconnect = async () => {clear(); await initialize();};
  useEffect(() => {void initialize();}, [initialize]);
  useEffect(() => {
    if (state.status !== 'active') return;
    document.documentElement.removeAttribute('data-resuming');
    const deadline = Math.min(Date.parse(state.data.security.session.expires_at), Date.parse(state.data.security.session.idle_expires_at));
    const timer = window.setTimeout(() => expired(), Math.max(0, deadline - Date.now()));
    const focus = () => {
      if (Date.now() >= deadline) expired();
      else void refresh();
    };
    const hide = () => {document.documentElement.setAttribute('data-resuming', 'true');};
    const show = (event: PageTransitionEvent) => {if (event.persisted) {clear(); void initialize();}};
    window.addEventListener('focus', focus); window.addEventListener('pagehide', hide); window.addEventListener('pageshow', show);
    return () => {window.clearTimeout(timer); window.removeEventListener('focus', focus); window.removeEventListener('pagehide', hide); window.removeEventListener('pageshow', show);};
  }, [state, clear, expired, refresh, initialize]);
  useEffect(() => {
    if (!('BroadcastChannel' in window)) return;
    const bus = new BroadcastChannel('agent-context-session'); channel.current = bus;
    bus.onmessage = () => {clear(); void initialize();};
    return () => {bus.close(); channel.current = null;};
  }, [clear, initialize]);

  const act = async <T,>(path: string, schema: z.ZodType<T>, body: unknown, method: 'POST' | 'PATCH' | 'PUT' = 'POST') => {
    try {return await api.request(path, schema, {body, method});}
    catch (error) {
      if (error instanceof ApiError) {
        if (error.csrf) {await api.initialize(); await refresh();}
        else if (current.current.status === 'active' && (error.status === 401 || error.status === 409)) {const updated = await refresh(); if (updated) publish({status: 'active', data: updated, message: errorText(error)});}
      }
      throw error;
    }
  };
  const read = async <T,>(path: string, schema: z.ZodType<T>, signal?: AbortSignal) => {
    try {return await api.request(path, schema, {signal});}
    catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 409)) await refresh();
      throw error;
    }
  };
  const accept = async (result: SessionResult) => {
    const stay = current.current.status === 'active' ? location.pathname : null;
    clear(); api.issued(result);
    const data = await refresh();
    if (data) {
      setCodes(result.recovery_codes ?? []);
      navigate(stay ?? '/o/' + encodeURIComponent(data.me.policy_context.org_id) + '/workspace', {replace: true});
      channel.current?.postMessage('session-changed');
    }
  };
  const provider = async (org: string, purpose: 'login' | 'link' | 'switch' | 'reauth') => {
    clear('switching');
    try {
      const path = purpose === 'login' ? '/auth/oidc/start' : purpose === 'link' ? '/auth/identities/link' : '/auth/oidc/reauthenticate';
      const result = await api.request(path, redirectSchema, {body: {org_id: org, ...(['switch', 'reauth'].includes(purpose) ? {purpose} : {})}});
      providerNavigate(result.authorization_url);
    } catch (error) {
      if (error instanceof StaleContext) throw error;
      const data = await refresh();
      publish(data ? {status: 'active', data, message: errorText(error)} : {status: current.current.status === 'unavailable' ? 'unavailable' : 'anonymous', message: errorText(error)});
    }
  };
  const switchOrganization = async (target: Organization) => {
    const before = current.current;
    if (before.status !== 'active' || !before.data.organizations.some(org => org.org_id === target.org_id)) return;
    clear('switching');
    try {
      const result = await api.request('/me/active-organization', sessionResultSchema, {body: {org_id: target.org_id}});
      await accept(result);
    } catch (error) {
      const data = await refresh();
      if (data && error instanceof ApiError && error.status === 401) setTargetProvider(target);
      else if (!data) expired();
      else {publish({status: 'active', data, message: errorText(error)}); throw error;}
    }
  };
  const logout = async () => {
    const oidc = current.current.status === 'active' && current.current.data.me.auth_method === 'oidc';
    try {
      if (oidc) {
        const result = await api.request('/auth/oidc/logout', logoutSchema, {body: {}});
        expired('You signed out.'); channel.current?.postMessage('session-changed'); providerNavigate(result.end_session_url);
      } else {await api.request('/auth/logout', voidSchema, {body: {}}); expired('You signed out.'); channel.current?.postMessage('session-changed');}
    } catch {expired(); await api.initialize();}
  };
  const value: Auth = {state, api, generation: api.generation, refresh, reconnect, act, read, accept, switchOrganization, provider, logout, expired, dirty, setDirty};
  return <Context.Provider value={value}>{children}
    <Modal title="Save your recovery codes" description="These codes are shown once. Store them in your password manager. Each code works once." open={codes.length > 0} onOpenChange={open => {if (!open) setCodes([]);}}>
      <ul className="recovery-codes">{codes.map(code => <li key={code}><code>{code}</code></li>)}</ul>
      <div className="actions"><Button variant="secondary" onClick={() => {void navigator.clipboard.writeText(codes.join('\n'));}}>Copy codes</Button><Button onClick={() => setCodes([])}>I saved these codes</Button></div>
    </Modal>
    <Modal title="Organization sign-in required" description={`${targetProvider?.name ?? 'This organization'} requires its approved sign-in. Your previous context has been cleared.`} open={targetProvider !== null} onOpenChange={open => {if (!open) setTargetProvider(null);}}>
      <div className="actions"><Button variant="secondary" onClick={() => setTargetProvider(null)}>Stay in current organization</Button><Button onClick={() => {if (targetProvider) void provider(targetProvider.org_id, 'switch').catch(() => setTargetProvider(null));}}>Continue to approved sign-in</Button></div>
    </Modal>
  </Context.Provider>;
}


export function SessionUnavailable() {
  const auth = useAuth();
  return <main id="main-content" className="loading-screen"><h1>Session verification unavailable</h1><Notice tone="warning">We couldn’t verify your session. Reconnect before continuing.</Notice><Button onClick={() => {void auth.reconnect();}}>Retry connection</Button></main>;
}
