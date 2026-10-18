import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { fakeApi, mount, reply } from '../test/harness';
import { monitoringFixture, summary } from './testing';
describe('declared monitoring sources',()=>{
 for(const state of ['missing','unavailable','stale','absent_history','partial','complete'] as const)it('keeps '+state+' distinct from fabricated zeros',async()=>{
  fakeApi(monitoringFixture(),path=>{if(path.includes('/overview?'))return reply({...summary,observed_count:0,outcomes:{},buckets:[],coverage:{...summary.coverage,state,gaps:state==='partial'?[{start:summary.query.start,end:summary.query.end}]:[]}});if(path.includes('/effective-access'))return reply({subject_id:'person',policy_revision:2,tuples:[],assignments:[],role_has_corpus_bypass:false});});mount('/o/org-a/leadership?timezone=Asia%2FTehran');
  expect(await screen.findByText('0 recorded gateway reports')).toBeVisible();expect(screen.getByText('No recorded buckets')).toBeVisible();expect(screen.getByText(/Sample population:/)).toHaveTextContent(summary.coverage.population);expect(screen.getAllByText('Asia/Tehran').length).toBeGreaterThan(0);expect(screen.getByRole('table',{name:'Declared measures and sample population'})).toHaveTextContent('1 / 2');expect(screen.getByRole('table',{name:'Declared measures and sample population'})).toHaveTextContent('Unavailable');expect(screen.queryByRole('img',{name:/recorded events across/})).not.toBeInTheDocument();
  expect(screen.getByText(state==='complete'?/Zero recorded events in a completely declared/:/No recorded observations; coverage/)).toBeVisible();if(state==='partial')expect(screen.getByRole('table',{name:'Declared missing intervals'})).toBeVisible();
 });
 it('renders only server buckets with a textual summary and exact table alternative',async()=>{
  fakeApi(monitoringFixture(),path=>{if(path.includes('/overview?'))return reply(summary);if(path.includes('/effective-access'))return reply({subject_id:'person',policy_revision:2,tuples:[],assignments:[],role_has_corpus_bypass:false});});mount('/o/org-a/leadership?timezone=Asia%2FTehran');expect(await screen.findByRole('img',{name:/2 recorded events across 1 sparse buckets/})).toBeVisible();expect(screen.getByRole('table',{name:'Recorded intervals (chart alternative)'}).querySelectorAll('tbody tr')).toHaveLength(1);expect(screen.getByRole('link',{name:'Inspect interval'})).toHaveAttribute('href',expect.stringContaining('timezone=Asia%2FTehran'));expect(screen.queryByText('PRIVATE_FOREIGN')).not.toBeInTheDocument();
 });
});
