import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { AuthProvider, SessionUnavailable, useAuth } from './auth/context';
import { AuthScreen } from './auth/AuthScreen';
import { MyAccess, Protected, Workspace } from './shell/Shell';
import { MySecurity } from './security/MySecurity';
import { Specimens } from './components/Specimens';
import { Button, Notice, Skeleton } from './components/ui';
import { People, Person, Teams, Team } from './people/People';
import { AccessRequests, Invitations, MyInvitations, Ownership } from './access/Access';
import { Clients, ClientDetail, ServiceAccounts } from './clients/Clients';
import { Templates } from './clients/Templates';
import { Bindings } from './clients/Bindings';
import './styles.css';

export function Entry() {
  const auth = useAuth();
  if (auth.state.status === 'loading' || auth.state.status === 'switching') return <main id="main-content" className="loading-screen"><Skeleton label="Checking your organization session"/></main>;
  if (auth.state.status === 'unavailable') return <SessionUnavailable/>;
  return <Navigate to={auth.state.status === 'active' ? `/o/${encodeURIComponent(auth.state.data.me.policy_context.org_id)}/workspace` : '/sign-in'} replace/>;
}
function CallbackError() {
  const auth = useAuth(); const navigate = useNavigate();
  return <main id="main-content" className="loading-screen"><Notice tone="danger">Provider sign-in was not accepted. The request may have expired, been reused, or come from a different context.</Notice><Button onClick={() => {void auth.refresh().then(data => {if (data) navigate(`/o/${encodeURIComponent(data.me.policy_context.org_id)}/workspace`, {replace: true});});}}>Check current session</Button><a href="/console/sign-in">Return to sign in</a></main>;
}
export function App() {
  return <><a className="skip-link" href="#main-content">Skip to main content</a><Routes>
    <Route path="/" element={<Entry/>}/><Route path="/auth/complete" element={<Entry/>}/><Route path="/auth/error" element={<CallbackError/>}/>
    <Route path="/sign-in" element={<AuthScreen key="login"/>}/><Route path="/reset-request" element={<AuthScreen key="request" mode="reset-request"/>}/><Route path="/reset" element={<AuthScreen key="reset" mode="reset"/>}/><Route path="/invitation" element={<AuthScreen key="invite" mode="invite"/>}/><Route path="/verify" element={<AuthScreen key="verify" mode="verify"/>}/>
    <Route path="/specimens" element={<Specimens/>}/>
    <Route path="/o/:orgId" element={<Protected/>}><Route path="workspace" element={<Workspace/>}/><Route path="my-access" element={<MyAccess/>}/><Route path="security" element={<MySecurity/>}/><Route path="people" element={<People/>}/><Route path="people/:userId" element={<Person/>}/><Route path="teams" element={<Teams/>}/><Route path="teams/:teamId" element={<Team/>}/><Route path="invitations" element={<Invitations/>}/><Route path="my-invitations" element={<MyInvitations/>}/><Route path="access-requests" element={<AccessRequests/>}/><Route path="ownership" element={<Ownership/>}/><Route path="clients" element={<Clients/>}/><Route path="clients/:clientId" element={<ClientDetail/>}/><Route path="service-accounts" element={<ServiceAccounts/>}/><Route path="agent-templates" element={<Templates/>}/><Route path="bindings" element={<Bindings/>}/><Route path="*" element={<Entry/>}/></Route>
    <Route path="*" element={<Entry/>}/>
  </Routes></>;
}
const element = document.getElementById('root');
if (element) {
  const queries = new QueryClient({defaultOptions: {queries: {retry: false, staleTime: 0, gcTime: 0, refetchOnWindowFocus: false}, mutations: {retry: false}}});
  createRoot(element).render(<StrictMode><QueryClientProvider client={queries}><BrowserRouter basename="/console"><AuthProvider><App/></AuthProvider></BrowserRouter></QueryClientProvider></StrictMode>);
}
