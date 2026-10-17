import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { fakeApi, fixture, mount, reply } from '../test/harness';

describe('personal security', () => {
  it('shows only safe identity/session metadata and confirms current-session revocation', async () => {
    const user = userEvent.setup(); const api = fakeApi(fixture(), path => {
      if (path === '/me/sessions/session-org-a/revoke') {api.setActive(null); return new Response(null, {status: 204});}
    });
    mount('/o/org-a/security');
    expect(await screen.findByRole('heading', {name: 'My security'})).toBeVisible();
    expect(await screen.findByText('This browser')).toBeVisible();
    expect(screen.queryByText('test-csrf')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: 'Revoke this browser session'}));
    const dialog = await screen.findByRole('dialog', {name: 'Revoke browser session?'});
    await user.click(within(dialog).getByRole('button', {name: 'Revoke session'}));
    expect(await screen.findByText('This browser session was revoked.')).toBeVisible();
    expect(screen.queryByRole('heading', {name: 'My security'})).not.toBeInTheDocument();
  });
  it('renders step-up restrictions and does not offer a local bypass for required SSO', async () => {
    const data = fixture('org-b'); data.me.auth_method = 'oidc'; data.security.auth_method = 'oidc'; data.security.step_up_required = true;
    fakeApi(data); mount('/o/org-b/security');
    expect(await screen.findByRole('button', {name: 'Reauthenticate with provider'})).toBeVisible();
    expect(screen.getByRole('button', {name: 'Change password'})).toBeDisabled();
    expect(screen.getByRole('button', {name: 'Replace recovery codes'})).toBeDisabled();
    expect(screen.getByText(/Local account changes do not change that entry policy/)).toBeVisible();
  });
  it('issues recovery codes once in memory, clears them on acknowledgement', async () => {
    const user = userEvent.setup(); const data = fixture();
    fakeApi(data, path => path === '/auth/mfa/recovery' ? reply({authenticated: true, csrf_token: 'new', policy_context: data.me.policy_context, recovery_codes: ['fictional-single-use-code']}) : undefined);
    mount('/o/org-a/security'); await user.type(await screen.findByLabelText('Fresh authenticator code'), '123456');
    await user.click(screen.getByRole('button', {name: 'Replace recovery codes'}));
    expect(await screen.findByRole('dialog', {name: 'Save your recovery codes'})).toBeVisible();
    expect(screen.getByText('fictional-single-use-code')).toBeVisible();
    await user.click(screen.getByRole('button', {name: 'I saved these codes'}));
    expect(screen.queryByText('fictional-single-use-code')).not.toBeInTheDocument();
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
  });
});
