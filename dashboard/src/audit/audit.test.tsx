import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { fakeApi, mount, reply } from '../test/harness';
import { event, monitoringFixture, page, summary } from '../overview/testing';
describe('redacted governance audit',()=>{
 it('renders recorded before/after authority only, with missing snapshots explicit',async()=>{
  fakeApi(monitoringFixture(),path=>{if(path.includes('/overview?'))return reply({...summary,query:{...summary.query,source:'governance'},outcomes:{committed:2},measures:{}});if(path.includes('/activity?'))return reply(page);if(path.includes('/audit-diff?'))return reply({event,fields:[{field:'revision',before:'1',after:'2'},{field:'tuples',before:null,after:'content:read / project: include (2 identifiers withheld)'}],state:'recorded_fields',coverage:page.coverage,redaction:'Target identities, corpus text and payloads withheld.'});});const {container}=mount('/o/org-a/audit');await userEvent.click(await screen.findByRole('button',{name:'Inspect authority diff event-a'}));expect(await screen.findByRole('table',{name:'Redacted authority changes'})).toHaveTextContent('Not recorded / withheld');expect(screen.getByText(/Target identities, corpus text/)).toBeVisible();expect(container.querySelector('.modal script')).toBeNull();
 });
 it('keeps historical diff unavailability distinct from a current policy snapshot',async()=>{
  fakeApi(monitoringFixture(),path=>{if(path.includes('/overview?'))return reply({...summary,query:{...summary.query,source:'governance'},outcomes:{committed:2},measures:{}});if(path.includes('/activity?'))return reply(page);if(path.includes('/audit-diff?'))return reply({event,fields:[],state:'unavailable',coverage:page.coverage,redaction:'Fields withheld.'});});mount('/o/org-a/audit');await userEvent.click(await screen.findByRole('button',{name:'Inspect authority diff event-a'}));expect(await screen.findByText('No comparable authority fields recorded')).toBeVisible();expect(screen.queryByRole('table',{name:'Redacted authority changes'})).not.toBeInTheDocument();
 });
});
