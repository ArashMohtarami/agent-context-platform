import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { fakeApi, fixture, mount, reply } from '../test/harness';
import { authoring, page } from '../management/testing';
describe('people boundary and revision UX', () => {
  it('renders genuine empty data and disables unauthorized invitation affordance', async () => {
    fakeApi(fixture(), path => path.includes('/members?') ? reply(page([])) : undefined); mount('/o/org-a/people');
    expect(await screen.findByText('No people in this view')).toBeVisible();
    expect(screen.getByRole('button', {name: 'Invite a person'})).toBeDisabled();
  });
  it('shows only the permitted directory page and sends the reviewed membership revision after confirmation', async () => {
    const user = userEvent.setup(); const data = fixture(); data.grants = [{action: 'members:read', resource_kind: 'member', selector: 'include', ids: ['worker']}];
    const member = {org_id: 'org-a', user_id: 'worker', display_name: 'Fictional Worker', role: 'contributor', status: 'active', revision: 7};
    const api = fakeApi(data, (path, init) => {
      if (path.endsWith('/management/context')) return reply(authoring());
      if (path.endsWith('/management/members/worker')) return reply({member, teams: [], actions: ['members:suspend']});
      if (path.endsWith('/members/worker/suspend')) {expect(JSON.parse(String(init.body))).toEqual({expected_revision: 7}); return reply({});}
      return undefined;
    }); mount('/o/org-a/people/worker');
    expect(await screen.findByRole('heading', {name: 'Fictional Worker'})).toBeVisible();
    expect(screen.getByRole('button', {name: 'Assign bounded policy'})).toBeDisabled();
    await user.click(screen.getByRole('button', {name: 'Suspend membership'}));
    expect(api.requests.some(r => r.path.endsWith('/suspend'))).toBe(false);
    await user.click(screen.getByRole('button', {name: 'Confirm suspend membership'}));
    expect(api.requests.filter(r => r.path.endsWith('/suspend'))).toHaveLength(1);
  });
});
