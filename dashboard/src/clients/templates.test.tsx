import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { fakeApi, fixture, mount, reply } from '../test/harness';
import { page } from '../management/testing';
describe('copy-on-assignment agent templates', () => {
  it('shows actual assignment impact before a revision-bound disable and labels rate profiles honestly', async () => {
    const user = userEvent.setup(); const data = fixture(); data.grants = [{action: 'clients:manage:org', resource_kind: 'organization', selector: 'include', ids: ['org-a']}];
    const template = {profile_id: 'coding', name: 'Fictional Coding', scopes: ['context:read'], status: 'active', revision: 5, rate_limit_profile: null};
    const api = fakeApi(data, (path, init) => {
      if (path.includes('/catalog/templates?')) return reply(page([template]));
      if (path.endsWith('/templates/coding/impact')) return reply({profile_id: 'coding', revision: 5, assigned_client_count: 3, assigned_clients_changed: false, rate_limit_profile_enforced: false});
      if (path.endsWith('/client-templates/coding')) {expect(init.method).toBe('PUT'); expect(JSON.parse(String(init.body))).toMatchObject({expected_revision: 5, status: 'disabled', scopes: ['context:read']}); return reply({});}
      return undefined;
    }); mount('/o/org-a/agent-templates');
    await user.click(await screen.findByRole('button', {name: 'Edit template / impact'}));
    expect(screen.getByText('Descriptive only. This field does not enforce a rate limit.')).toBeVisible();
    await user.selectOptions(screen.getByLabelText('Template status'), 'disabled');
    await user.click(screen.getByRole('button', {name: 'Review template impact'}));
    expect(await screen.findByText(/3 assigned clients retain their existing copied grants/)).toBeVisible();
    expect(api.requests.some(r => r.init.method === 'PUT')).toBe(false);
    await user.click(screen.getByRole('button', {name: 'Confirm template change'}));
    expect(api.requests.filter(r => r.init.method === 'PUT')).toHaveLength(1);
  });
});
