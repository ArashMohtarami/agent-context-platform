import { lazy, StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { AuthProvider, SessionUnavailable, useAuth } from './auth/context';
import { AuthScreen } from './auth/AuthScreen';
import { MyAccess, Protected, Workspace } from './shell/Shell';
const MySecurity = lazy(() => import('./security/MySecurity').then(module => ({default: module.MySecurity}), () => ({default: WorkspaceUnavailable})));
import { Specimens } from './components/Specimens';
import { Button, Notice, Skeleton, WorkspaceUnavailable } from './components/ui';
const People = lazy(() => import('./people/People').then(module => ({default: module.People}), () => ({default: WorkspaceUnavailable})));
const Person = lazy(() => import('./people/People').then(module => ({default: module.Person}), () => ({default: WorkspaceUnavailable})));
const Teams = lazy(() => import('./people/People').then(module => ({default: module.Teams}), () => ({default: WorkspaceUnavailable})));
const Team = lazy(() => import('./people/People').then(module => ({default: module.Team}), () => ({default: WorkspaceUnavailable})));
const AccessRequests = lazy(() => import('./access/Access').then(module => ({default: module.AccessRequests}), () => ({default: WorkspaceUnavailable})));
const Invitations = lazy(() => import('./access/Access').then(module => ({default: module.Invitations}), () => ({default: WorkspaceUnavailable})));
const MyInvitations = lazy(() => import('./access/Access').then(module => ({default: module.MyInvitations}), () => ({default: WorkspaceUnavailable})));
const Ownership = lazy(() => import('./access/Access').then(module => ({default: module.Ownership}), () => ({default: WorkspaceUnavailable})));
const Clients = lazy(() => import('./clients/Clients').then(module => ({default: module.Clients}), () => ({default: WorkspaceUnavailable})));
const ClientDetail = lazy(() => import('./clients/Clients').then(module => ({default: module.ClientDetail}), () => ({default: WorkspaceUnavailable})));
const ServiceAccounts = lazy(() => import('./clients/Clients').then(module => ({default: module.ServiceAccounts}), () => ({default: WorkspaceUnavailable})));
const Templates = lazy(() => import('./clients/Templates').then(module => ({default: module.Templates}), () => ({default: WorkspaceUnavailable})));
const Bindings = lazy(() => import('./clients/Bindings').then(module => ({default: module.Bindings}), () => ({default: WorkspaceUnavailable})));
const Projects = lazy(() => import('./knowledge/Projects').then(module => ({default: module.Projects}), () => ({default: WorkspaceUnavailable})));
const ProjectDetail = lazy(() => import('./knowledge/Projects').then(module => ({default: module.ProjectDetail}), () => ({default: WorkspaceUnavailable})));
const Skills = lazy(() => import('./knowledge/Skills').then(module => ({default: module.Skills}), () => ({default: WorkspaceUnavailable})));
const SkillDetail = lazy(() => import('./knowledge/Skills').then(module => ({default: module.SkillDetail}), () => ({default: WorkspaceUnavailable})));
const MemoryWorkspace = lazy(() => import('./knowledge/Memory').then(module => ({default: module.MemoryWorkspace}), () => ({default: WorkspaceUnavailable})));
const KnowledgeSearch = lazy(() => import('./knowledge/Search').then(module => ({default: module.KnowledgeSearch}), () => ({default: WorkspaceUnavailable})));
const ResearchProjects = lazy(() => import('./research/Research').then(module => ({default: module.ResearchProjects}), () => ({default: WorkspaceUnavailable})));
const ResearchWorkspace = lazy(() => import('./research/Research').then(module => ({default: module.ResearchWorkspace}), () => ({default: WorkspaceUnavailable})));
const CreatorProfiles = lazy(() => import('./creator/Creator').then(module => ({default: module.CreatorProfiles}), () => ({default: WorkspaceUnavailable})));
const CreatorWorkspace = lazy(() => import('./creator/Creator').then(module => ({default: module.CreatorWorkspace}), () => ({default: WorkspaceUnavailable})));
const CreatorItem = lazy(() => import('./creator/Item').then(module => ({default: module.CreatorItem}), () => ({default: WorkspaceUnavailable})));
const Overview = lazy(() => import('./overview/Overview').then(module => ({default: module.Overview}), () => ({default: WorkspaceUnavailable})));
const Activity = lazy(() => import('./activity/Activity').then(module => ({default: module.Activity}), () => ({default: WorkspaceUnavailable})));
const MonitoringPolicy = lazy(() => import('./overview/Policy').then(module => ({default: module.MonitoringPolicy}), () => ({default: WorkspaceUnavailable})));
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
    <Route path="/o/:orgId" element={<Protected/>}><Route path="team-overview" element={<Overview scope="team"/>}/><Route path="leadership" element={<Overview scope="org"/>}/><Route path="activity" element={<Activity/>}/><Route path="audit" element={<Activity audit/>}/><Route path="monitoring-policy" element={<MonitoringPolicy/>}/><Route path="creator" element={<CreatorProfiles/>}/><Route path="creator/:profileId" element={<CreatorWorkspace/>}/><Route path="creator/:profileId/items/:contentId" element={<CreatorItem/>}/><Route path="research" element={<ResearchProjects/>}/><Route path="research/:researchId" element={<ResearchWorkspace/>}/><Route path="knowledge/projects" element={<Projects/>}/><Route path="knowledge/projects/:projectId" element={<ProjectDetail/>}/><Route path="knowledge/skills" element={<Skills/>}/><Route path="knowledge/skills/:skillId" element={<SkillDetail/>}/><Route path="knowledge/memory" element={<MemoryWorkspace/>}/><Route path="knowledge/search" element={<KnowledgeSearch/>}/><Route path="workspace" element={<Workspace/>}/><Route path="my-access" element={<MyAccess/>}/><Route path="security" element={<MySecurity/>}/><Route path="people" element={<People/>}/><Route path="people/:userId" element={<Person/>}/><Route path="teams" element={<Teams/>}/><Route path="teams/:teamId" element={<Team/>}/><Route path="invitations" element={<Invitations/>}/><Route path="my-invitations" element={<MyInvitations/>}/><Route path="access-requests" element={<AccessRequests/>}/><Route path="ownership" element={<Ownership/>}/><Route path="clients" element={<Clients/>}/><Route path="clients/:clientId" element={<ClientDetail/>}/><Route path="service-accounts" element={<ServiceAccounts/>}/><Route path="agent-templates" element={<Templates/>}/><Route path="bindings" element={<Bindings/>}/><Route path="*" element={<Entry/>}/></Route>
    <Route path="*" element={<Entry/>}/>
  </Routes></>;
}
const element = document.getElementById('root');
if (element) {
  const queries = new QueryClient({defaultOptions: {queries: {retry: false, staleTime: 0, gcTime: 0, refetchOnWindowFocus: false}, mutations: {retry: false}}});
  createRoot(element).render(<StrictMode><QueryClientProvider client={queries}><BrowserRouter basename="/console"><AuthProvider><App/></AuthProvider></BrowserRouter></QueryClientProvider></StrictMode>);
}
