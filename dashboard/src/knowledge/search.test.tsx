import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { fakeApi, fixture, mount, reply } from '../test/harness';
import { SafeMarkdown, safeKnowledgeUrl } from './Markdown';
import { knowledgeFixture, searchResult } from './testing';

describe('knowledge search', () => {
  it('renders CommonMark without executable HTML, unsafe schemes or remote images', () => {
    const {container} = render(<SafeMarkdown>{searchResult.results[0]!.content}</SafeMarkdown>);
    expect(screen.getByText('Safe bold').tagName).toBe('STRONG');
    expect(container.querySelector('script,iframe,img,svg,object,style')).toBeNull();
    expect(screen.queryByRole('link', {name: 'bad'})).not.toBeInTheDocument();
    expect(screen.getByRole('link', {name: /Good/})).toHaveAttribute('rel', 'noopener noreferrer');
    for (const url of ['javascript:alert(1)', 'data:text/html,x', 'vbscript:x', '//foreign.test', '/api/delete', 'https://u:p@example.test', 'java\nscript:x', 'file:///tmp/x']) expect(safeKnowledgeUrl(url)).toBe('');
  });
  it('posts only session APIs with explicit source/filter/budget and real provenance', async () => {
    const user = userEvent.setup();
    const api = fakeApi(knowledgeFixture(), (path, init) => path.endsWith('/knowledge/search') ? (expect(init.credentials).toBe('same-origin'), reply(searchResult)) : undefined);
    mount('/o/org-a/knowledge/search?project=a');
    await user.type(await screen.findByLabelText('Search terms'), 'isolation');
    await user.selectOptions(screen.getByLabelText('Search source'), 'project_context');
    await user.click(screen.getByRole('button', {name: 'Search knowledge'}));
    expect(await screen.findByRole('heading', {name: '1 permitted results'})).toBeVisible();
    expect(screen.getByText('context:a:permitted')).toBeVisible();
    const request = api.requests.find(r => r.path.endsWith('/knowledge/search'))!;
    expect(JSON.parse(String(request.init.body))).toMatchObject({project_id: 'a', sources: ['project_context'], token_budget: 2048, memory_scopes: ['global', 'project']});
    expect(new Headers(request.init.headers).get('X-CSRF-Token')).toBe('test-csrf');
    expect(new Headers(request.init.headers).has('Authorization')).toBe(false);
    expect(screen.queryByRole('link', {name: 'bad'})).not.toBeInTheDocument();
  });
  it('discards a delayed old-org search and sensitive query after an explicit switch', async () => {
    const user = userEvent.setup(); let release: (r: Response) => void = () => {};
    const next = fixture('org-b');
    const api = fakeApi(knowledgeFixture(), path => {
      if (path.endsWith('/knowledge/search')) return new Promise<Response>(resolve => {release = resolve;});
      if (path === '/me/active-organization') {api.setActive(next); return reply({authenticated: true, csrf_token: 'new', policy_context: next.me.policy_context});}
    });
    const {queries} = mount('/o/org-a/knowledge/search?project=a');
    await user.type(await screen.findByLabelText('Search terms'), 'private query');
    await user.click(screen.getByRole('button', {name: 'Search knowledge'}));
    await user.selectOptions(screen.getByLabelText('Active organization'), 'org-b');
    await user.click(await screen.findByRole('button', {name: 'Discard and switch'}));
    await act(async () => {release(reply(searchResult));});
    expect(screen.queryByText('Permitted citation')).not.toBeInTheDocument();
    expect(JSON.stringify(queries.getQueryCache().getAll().map(q => q.state.data))).not.toContain('context:a:permitted');
    expect(screen.getByLabelText('Active organization')).toHaveValue('org-b');
  });
});
