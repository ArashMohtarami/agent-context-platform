import { act, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { fakeApi, fixture, mount, reply } from '../test/harness';
import { managedClient, page, readA } from '../management/testing';

const recipe = {configuration_text: '[mcp_servers.agent-context]', configuration: {mcp_servers: {'agent-context': {env: {MCP_API_BASE_URL: 'https://example.test'}}}}, doctor_command: 'contextctl integration doctor codex --connect', stages: ['configuration', 'transport', 'authentication', 'capabilities', 'project', 'retrieval'], next_steps: ['Recipient-only delivery'], preview_only: true, configuration_applied: false, policy_changed: false, server_read_only_verified: false, approved_client_read_only: true};
function setup(answer = () => Promise.resolve(reply(recipe))) {
  return fakeApi(fixture(), (path, init) => {
    if (path.endsWith('/managed-clients/coding-worker')) return reply({client: managedClient(), credentials: [], effective_tuples: [readA], rate_limit_profile_enforced: false});
    if (path.includes('/catalog/sessions?')) return reply(page([]));
    if (path.includes('/connection-preview?')) {expect(init.method).toBe('GET'); return answer();}
    return undefined;
  });
}
async function selection() {
  const user = userEvent.setup();
  await user.selectOptions(await screen.findByLabelText('Connection client'), 'codex');
  await user.clear(screen.getByLabelText('Installation origin'));
  await user.type(screen.getByLabelText('Installation origin'), 'https://example.test');
  await user.type(screen.getByLabelText('Explicit connection project ID'), 'fictional');
  await user.click(screen.getByLabelText('Require server-approved read-only access'));
  return user;
}

describe('preview-only client connection', () => {
  it('requires explicit selection, uses session GET and leaves grants and credentials untouched', async () => {
    const server = setup(); const mounted = mount('/o/org-a/clients/coding-worker'); const user = await selection();
    expect(screen.queryByLabelText('Connection recipe')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: 'Preview connection recipe'}));
    expect(await screen.findByLabelText('Connection doctor command')).toHaveTextContent('--connect');
    expect(screen.getByText(/configuration: not checked in preview/)).toBeVisible();
    const request = server.requests.find(r => r.path.includes('/connection-preview?'))!;
    const params = new URL(request.path, 'https://example.test').searchParams;
    expect(params.get('origin')).toBe('https://example.test'); expect(params.get('project_id')).toBe('fictional'); expect(params.get('require_read_only')).toBe('true');
    expect(server.requests.filter(r => r.init.method === 'POST')).toHaveLength(0);
    expect(JSON.stringify(mounted.queries.getQueryCache().getAll().map(q => q.state.data))).not.toContain('doctor_command');
    await user.type(screen.getByLabelText('Explicit connection project ID'), '-changed');
    expect(screen.queryByLabelText('Connection recipe')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Bearer token')).not.toBeInTheDocument();
  });
  it('discards a stale recipe when selection changes during the request', async () => {
    let deliver!: (value: Response) => void;
    setup(() => new Promise<Response>(resolve => {deliver = resolve;}));
    mount('/o/org-a/clients/coding-worker'); const user = await selection();
    await user.click(screen.getByRole('button', {name: 'Preview connection recipe'}));
    await user.type(screen.getByLabelText('Explicit connection project ID'), '-new');
    await act(async () => {deliver(reply(recipe));});
    expect(screen.queryByLabelText('Connection recipe')).not.toBeInTheDocument();
  });
  it('shows a safe refusal without minting a credential or relaxing policy', async () => {
    const server = setup(() => Promise.resolve(reply({detail: 'forbidden'}, 403)));
    mount('/o/org-a/clients/coding-worker'); const user = await selection();
    await user.click(screen.getByRole('button', {name: 'Preview connection recipe'}));
    expect(await screen.findByText(/outside your current access/i)).toBeVisible();
    expect(screen.queryByLabelText('Connection recipe')).not.toBeInTheDocument();
    expect(server.requests.filter(r => r.init.method === 'POST')).toHaveLength(0);
  });
});
