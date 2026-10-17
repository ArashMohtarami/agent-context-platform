import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { fakeApi, mount, reply } from '../test/harness';
import { context, knowledgeFixture, memory, project } from './testing';

describe('knowledge writes and history', () => {
  it('keeps private scopes disabled and read-A/write-B controls coupled', async () => {
    const user = userEvent.setup();
    fakeApi(knowledgeFixture(), path => path.includes('/knowledge/memories?') ? reply({scope: 'project', project_id: 'a', client_id: null, items: [memory], next_cursor: null}) : undefined);
    mount('/o/org-a/knowledge/memory?project=a');
    await screen.findByRole('heading', {name: 'Fictional decision'});
    expect(screen.queryByRole('button', {name: 'Create memory'})).not.toBeInTheDocument();
    expect(screen.getByRole('option', {name: 'Agent — machine identity required'})).toBeDisabled();
    expect(screen.getByRole('option', {name: 'Project-agent — machine identity required'})).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Project', {exact: true}), 'b');
    expect(screen.getByRole('button', {name: 'Create memory'})).toBeEnabled();
    expect(screen.queryByRole('heading', {name: 'Fictional decision'})).not.toBeInTheDocument();
  });
  it('records an intentional same-scope supersession without choosing a client or org', async () => {
    const user = userEvent.setup();
    const api = fakeApi(knowledgeFixture(), (path, init) => {
      if (path.includes('/knowledge/memories?')) return reply({scope: 'global', project_id: null, client_id: null, items: [memory], next_cursor: null});
      if (path.endsWith('/knowledge/memories') && init.method === 'POST') return reply({...memory, memory_id: 'c'.repeat(24), content: 'Correction', supersedes: memory.memory_id});
    });
    mount('/o/org-a/knowledge/memory');
    await user.click(await screen.findByRole('button', {name: 'Supersede memory'}));
    await user.type(screen.getByLabelText('Memory Markdown'), 'Correction');
    await user.click(screen.getByRole('button', {name: 'Record replacement'}));
    const request = api.requests.find(r => r.path.endsWith('/knowledge/memories') && r.init.method === 'POST')!;
    expect(JSON.parse(String(request.init.body))).toMatchObject({scope: 'global', supersedes: memory.memory_id, content: 'Correction'});
    expect(JSON.parse(String(request.init.body))).not.toHaveProperty('client_id');
    expect(JSON.parse(String(request.init.body))).not.toHaveProperty('org_id');
  });
  it('preserves a context draft on 409 and requires explicit comparison before retry', async () => {
    const user = userEvent.setup(); const data = knowledgeFixture(); data.grants.push({action: 'context:write', resource_kind: 'project', selector: 'include', ids: ['a']});
    let current = false;
    const api = fakeApi(data, (path, init) => {
      if (path.endsWith('/knowledge/projects/a')) return reply(project);
      if (path.includes('/knowledge/context?')) return reply({project_id: 'a', items: [context], next_cursor: null});
      if (path.includes('/knowledge/context/' + context.context_id)) {
        if (init.method === 'PATCH') {current = true; return reply({error: {code: 'revision_conflict'}}, 409);}
        return reply({...context, revision: current ? 3 : 2, content: 'Concurrent saved text'});
      }
    });
    mount('/o/org-a/knowledge/projects/a');
    await user.click(await screen.findByRole('button', {name: 'Edit Fictional context'}));
    await user.clear(screen.getByLabelText('Context Markdown')); await user.type(screen.getByLabelText('Context Markdown'), 'My unsaved draft');
    await user.click(screen.getByRole('button', {name: 'Save context'}));
    await screen.findByText(/Revision conflict. Your unsaved text is preserved/);
    expect(screen.getByLabelText('Context Markdown')).toHaveValue('My unsaved draft');
    expect(screen.getByRole('button', {name: 'Save context'})).toBeDisabled();
    await user.click(screen.getByRole('button', {name: 'Load current revision'}));
    await screen.findByText('Concurrent saved text');
    await user.click(screen.getByRole('button', {name: 'Use this revision with my draft'}));
    expect(screen.getByLabelText('Context Markdown')).toHaveValue('My unsaved draft');
    expect(screen.getByRole('button', {name: 'Save context'})).toBeEnabled();
    const request = api.requests.find(r => r.init.method === 'PATCH')!;
    expect(JSON.parse(String(request.init.body)).expected_revision).toBe(2);
  });
});
