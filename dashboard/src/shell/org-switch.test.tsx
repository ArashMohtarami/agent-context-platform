import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { SessionApi, StaleContext, namespace } from '../api/client';
import { effectiveSchema } from '../api/contracts';
import { fakeApi, fixture, mount, reply } from '../test/harness';

describe('organization boundary', () => {
  it('rejects late responses even when the transport ignores cancellation', async () => {
    let release: (response: Response) => void = () => {throw new Error('request not started');};
    fakeApi(fixture(), path => path === '/slow' ? new Promise<Response>(resolve => {release = resolve;}) : undefined);
    const api = new SessionApi(); const pending = api.request('/slow', effectiveSchema);
    api.clear();
    release(reply({org_id: 'org-a', subject_id: 'person', policy_revision: 1, tuples: [], role_has_corpus_bypass: false}));
    await expect(pending).rejects.toBeInstanceOf(StaleContext);
    expect(namespace(fixture('org-a'), 0)).not.toEqual(namespace(fixture('org-b'), 0));
    const changed = fixture(); changed.me.policy_revision = 3;
    expect(namespace(fixture(), 0)).not.toEqual(namespace(changed, 0));
    const policy = fixture(); policy.me.policy_context.policy_version++;
    expect(namespace(fixture(), 0)).not.toEqual(namespace(policy, 0));
  });
  it('clears old cached data and reloads navigation after switch', async () => {
    const user = userEvent.setup(); const next = fixture('org-b');
    const api = fakeApi(fixture(), path => {
      if (path === '/me/active-organization') {api.setActive(next); return reply({authenticated: true, csrf_token: 'new', policy_context: next.me.policy_context});}
    });
    const {queries} = mount('/o/org-a/workspace');
    await screen.findByRole('heading', {name: 'Welcome, Fictional Alice'});
    queries.setQueryData(['organization', 'org-a', 'private-sentinel'], 'alpha-only');
    await user.selectOptions(screen.getByLabelText('Active organization'), 'org-b');
    await screen.findByText('Approved SSO required');
    expect(queries.getQueryData(['organization', 'org-a', 'private-sentinel'])).toBeUndefined();
    expect(screen.getByLabelText('Active organization')).toHaveValue('org-b');
  });
  it('asks before discarding sensitive input and requires explicit target-provider authentication', async () => {
    const user = userEvent.setup(); const api = fakeApi(fixture(), path => path === '/me/active-organization' ? reply({detail: 'authentication was not accepted'}, 401) : undefined);
    mount('/o/org-a/security');
    await user.type(await screen.findByLabelText('Current password'), 'sensitive-draft');
    await user.selectOptions(screen.getByLabelText('Active organization'), 'org-b');
    expect(await screen.findByRole('dialog', {name: 'Discard unsaved changes?'})).toBeVisible();
    await user.click(screen.getByRole('button', {name: 'Keep editing'}));
    expect(screen.getByLabelText('Current password')).toHaveValue('sensitive-draft');
    expect(api.requests.some(row => row.path === '/me/active-organization')).toBe(false);
    await user.selectOptions(screen.getByLabelText('Active organization'), 'org-b');
    await user.click(screen.getByRole('button', {name: 'Discard and switch'}));
    expect(await screen.findByRole('dialog', {name: 'Organization sign-in required'})).toBeVisible();
    expect(screen.getByLabelText('Current password')).toHaveValue('');
    expect(api.requests.some(row => row.path === '/auth/oidc/reauthenticate')).toBe(false);
  });
});
