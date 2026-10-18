import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SessionApi, StaleContext } from '../api/client';
import { fixture, fakeApi, mount, reply } from '../test/harness';
import { downloadSchema } from '../overview/contracts';
import { event, monitoringFixture, page } from '../overview/testing';
import { ActorLabels } from './Activity';
describe('content-free event attribution',()=>{
 it('does not replace an event-time team with current membership or infer unknown history',()=>{
  render(<ActorLabels event={{...event,actor_kind:'machine',actor_id:'agent-a',sponsor_id:'worker',team_id:'old-team'}}/>);expect(screen.getByText('Verified actor: machine agent-a')).toBeVisible();expect(screen.getByText('Event-time human sponsor: worker')).toBeVisible();expect(screen.getByText('Event-time team: old-team')).toBeVisible();
 });
 it('uses the server total rather than page length and keeps legacy actors unknown',async()=>{
  fakeApi(monitoringFixture(),path=>{if(path.includes('/activity?'))return reply({...page,items:[{...event,attribution:'legacy_unattributed',actor_kind:'unknown',actor_id:null,team_id:null}]});});mount('/o/org-a/activity?scope=org');expect(await screen.findByText(/99 observed events.*1 on this page/)).toBeVisible();expect(screen.getByText(/Unknown — older unattributed event/)).toBeVisible();expect(screen.getByText('Event-time team: Not attributed')).toBeVisible();
 });
 it('rejects late report content after the organization generation changes',async()=>{
  let resolve!:(r:Response)=>void;vi.stubGlobal('fetch',()=>new Promise<Response>(r=>{resolve=r;}));const api=new SessionApi();api.context(fixture());const promise=api.download('/organizations/org-a/reports/report_00000000000000000000000000000000/download',downloadSchema);api.clear();resolve(new Response('PRIVATE_OLD_ORG_CSV',{headers:{'Content-Type':'text/csv','Cache-Control':'no-store','X-Summary-Metadata':'null'}}));await expect(promise).rejects.toBeInstanceOf(StaleContext);
 });
 it('does not offer reports when monitoring authority lacks export permission',async()=>{
  const f=monitoringFixture();f.grants=f.grants.filter(g=>g.action!=='reports:export');fakeApi(f,path=>path.includes('/activity?')?reply(page):undefined);mount('/o/org-a/activity?scope=org');expect(await screen.findByText(/Reports require a separate/)).toBeVisible();expect(screen.queryByRole('button',{name:'Prepare bounded CSV report'})).not.toBeInTheDocument();
 });
});
