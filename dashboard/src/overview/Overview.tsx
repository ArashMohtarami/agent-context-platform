import { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Button, Empty, ErrorState, Panel, Skeleton, Table } from '../components/ui';
import { accessSchema } from '../management/contracts';
import { id, useConsole, useRead } from '../management/shared';
import { Charts } from './Charts';
import { CoverageNotice, FiltersForm, MonitoringLinks } from './shared';
import { date, filterParams, initial, summarySchema } from './contracts';
import type { Filters } from './contracts';
export function Overview({scope='own',embedded=false}:{scope?:Filters['scope'];embedded?:boolean}) {
 const {allowed,data,org}=useConsole();const role=data.me.membership.role;const can=scope==='own'?allowed('activity:read:self','member',data.me.user_id):scope==='team'?allowed('activity:read:team','team'):role!=='team_lead'&&allowed('activity:read:org','organization',org);
 const title=scope==='own'?'My workspace':scope==='team'?'Team overview':'Leadership overview';
 return <>{!embedded&&<header className="page-heading"><span className="eyebrow">Authorized monitoring</span><h1>{title}</h1><p>Recorded observations within your current metadata permissions.</p></header>}<MonitoringLinks/>{embedded&&<h2>My workspace</h2>}{can?<OverviewData key={scope} scope={scope}/>:<Empty title="Monitoring access is not assigned">Your verified membership does not grant this monitoring view. Ask for the exact metadata action and resource policy.</Empty>}</>;
}
function OverviewData({scope}:{scope:Filters['scope']}) {
 const {base,data,route}=useConsole();const location=useLocation();const teams=data.grants.filter(g=>g.action==='activity:read:team'&&g.resource_kind==='team'&&g.selector==='include').flatMap(g=>g.ids);
 const [filters,setFilters]=useState(()=>initial(scope,teams,location.search));const query=useRead(base+'/overview?'+filterParams(filters),summarySchema,scope!=='team'||Boolean(filters.team_id));
 return <><FiltersForm value={filters} onApply={setFilters}/><Button variant="secondary" onClick={()=>{void query.refetch();}}>Refresh recorded observations</Button>{scope==='team'&&!filters.team_id?<Empty title="Choose an authorized event-time team">Apply a team ID within your current delegation.</Empty>:query.isPending?<Skeleton label="Loading permitted monitoring summary"/>:query.isError?<ErrorState onRetry={()=>{void query.refetch();}}>Monitoring is unavailable for these filters or your current policy. No zero is substituted.</ErrorState>:<><p className="hint">Summary last generated: {date(query.data.generated_at,filters.timezone)} · policy revision {query.data.policy_revision}</p><Charts summary={query.data}/><CoverageNotice coverage={query.data.coverage} zone={filters.timezone}/></>}<MyGrantExpiry/><Panel title="Monitoring policy"><p>Last seen describes a retained machine report, not employee presence or output. Leadership metadata grants do not reveal corpus bodies or private agent memory.</p><Link to={route+'/monitoring-policy'}>Read monitoring sources, privacy and retention</Link></Panel></>;
}
function MyGrantExpiry() {const {base,data,route}=useConsole();const query=useRead(base+'/members/'+id(data.me.user_id)+'/effective-access',accessSchema);return <Panel title="My grant expiry" description="Current own effective-access sources, separate from the historical filters. This is not a team-wide expiry count.">{query.isPending?<Skeleton/>:query.isError?<ErrorState>Current grant expiry sources are unavailable.</ErrorState>:<Table label="My current grant expiry sources" headings={['Assignment source','Current effectiveness','Expiry (UTC)']}>{query.data.assignments.map(a=><tr key={a.assignment_id}><td>{a.assignment_id}</td><td>{a.effective?'Effective':'Ineffective — '+(a.reason??'current policy')}</td><td>{a.expires_at?date(a.expires_at):'Not disclosed'}</td></tr>)}</Table>}<Link to={route+'/my-access'}>Inspect my effective access</Link></Panel>;}
