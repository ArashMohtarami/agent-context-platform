import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { fakeApi, fixture, mount, reply } from '../test/harness';

describe('session-only authentication', () => {
  it('completes password and MFA through real client boundaries without token storage', async () => {
    const user = userEvent.setup(); const data = fixture();
    const api = fakeApi(null, (path) => {
      if (path === '/auth/local/login') return reply({challenge: 'ephemeral-challenge', mfa_required: true});
      if (path === '/auth/local/complete') {api.setActive(data); return reply({authenticated: true, csrf_token: 'rotated', policy_context: data.me.policy_context});}
    });
    mount('/sign-in');
    await user.type(await screen.findByLabelText('Organization ID'), 'org-a');
    await user.type(screen.getByLabelText('Email address'), 'alice@example.test');
    await user.type(screen.getByLabelText('Password'), 'synthetic-password');
    await user.click(screen.getByRole('button', {name: 'Sign in'}));
    expect(await screen.findByRole('heading', {name: 'One more security check'})).toBeVisible();
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument();
    await user.type(screen.getByLabelText('Authentication code'), '123456');
    await user.click(screen.getByRole('button', {name: 'Verify and sign in'}));
    expect(await screen.findByRole('heading', {name: 'Welcome, Fictional Alice'})).toBeVisible();
    const posts = api.requests.filter(row => row.init.method === 'POST');
    expect(posts).toHaveLength(2);
    for (const row of posts) {expect(row.init.credentials).toBe('same-origin'); expect(row.init.headers).not.toHaveProperty('Authorization'); expect(row.init.headers).toHaveProperty('X-CSRF-Token', 'test-csrf');}
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
  });
  it('does not replay a mutation after a CSRF failure', async () => {
    const user = userEvent.setup(); const api = fakeApi(null, path => path === '/auth/password-reset/request' ? reply({detail: 'browser request was not accepted'}, 403) : undefined);
    mount('/reset-request'); await user.type(await screen.findByLabelText('Email address'), 'unknown@example.test');
    await user.click(screen.getByRole('button', {name: 'Request reset'}));
    expect(await screen.findByText('The security check failed. Review and submit again.')).toBeVisible();
    expect(api.requests.filter(row => row.path === '/auth/password-reset/request')).toHaveLength(1);
    await waitFor(() => expect(api.requests.filter(row => row.path === '/auth/csrf')).toHaveLength(2));
  });
  it('keeps a visible failure and usable retry when provider initiation fails', async () => {
    const user = userEvent.setup();
    fakeApi(null, path => path === '/auth/oidc/start' ? reply({detail: 'unavailable'}, 503) : undefined);
    mount('/sign-in');
    await user.type(await screen.findByLabelText('Organization ID'), 'org-a');
    await user.click(screen.getByRole('button', {name: 'Continue with SSO'}));
    expect(await screen.findByText('The service could not be reached. Try again when the connection is available.')).toBeVisible();
    await user.type(screen.getByLabelText('Organization ID'), 'org-a');
    expect(screen.getByRole('button', {name: 'Continue with SSO'})).toBeEnabled();
  });
  it('hides private content and avoids a false signed-out form when session verification is unavailable', async () => {
    const user = userEvent.setup(); let available = false;
    fakeApi(fixture(), path => path === '/me/console' && !available ? reply({detail: 'unavailable'}, 503) : undefined);
    mount('/o/org-a/workspace');
    expect(await screen.findByRole('heading', {name: 'Session verification unavailable'})).toBeVisible();
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument();
    expect(screen.queryByText('Fictional Alpha')).not.toBeInTheDocument();
    available = true; await user.click(screen.getByRole('button', {name: 'Retry connection'}));
    expect(await screen.findByRole('heading', {name: 'Welcome, Fictional Alice'})).toBeVisible();
  });
  it('keeps SSO navigation disabled until an organization is specified', async () => {
    fakeApi(null); mount('/sign-in');
    expect(await screen.findByRole('button', {name: 'Continue with SSO'})).toBeDisabled();
  });
});
