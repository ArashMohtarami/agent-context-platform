import { control, environment, expect, localLogin, noStoredCredentials, open, providerLogin, test } from './fixture';

test('local password + MFA, real logout and cookie/bearer separation', async ({page}) => {
  await control('local-only');
  const requests: string[] = [];
  page.on('request', request => {if (request.url().includes('/api/v6/')) requests.push(request.headers()['authorization'] ?? '');});
  await localLogin(page);
  await noStoredCredentials(page);
  const env = await environment();
  const denied = await page.request.get(env.origin + '/api/v1/projects');
  expect(denied.status()).toBe(401);
  await page.getByRole('button', {name: 'Sign out', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Your context, connected.'})).toBeVisible();
  expect(requests.every(value => value === '')).toBe(true);
  expect((await page.context().cookies(env.origin)).some(cookie => cookie.name === '__Host-acp-session')).toBe(false);
});

test('invitation, verification, login, real authenticator enrollment and one-time recovery', async ({page}) => {
  await open(page, '/console/invitation');
  await page.getByLabel('Invitation code', {exact: true}).fill(await control('mail', {purpose: 'invite', email: 'new@example.test'}));
  await page.getByLabel('Account password', {exact: true}).fill((await environment()).password);
  await page.getByRole('button', {name: 'Accept invitation', exact: true}).click();
  await expect(page.getByText('Continue with the verification message if the invitation was accepted.')).toBeVisible();
  await page.getByRole('link', {name: 'I have a verification code'}).click();
  await page.getByLabel('Verification code', {exact: true}).fill(await control('mail', {purpose: 'verify', email: 'new@example.test'}));
  await page.getByRole('button', {name: 'Verify account', exact: true}).click();
  await expect(page.getByText('Verification accepted. You can now sign in.')).toBeVisible();
  await page.getByRole('link', {name: 'Back to sign in'}).click();
  await page.getByLabel('Organization ID', {exact: true}).fill('org-a');
  await page.getByLabel('Email address', {exact: true}).fill('new@example.test');
  await page.getByLabel('Password', {exact: true}).fill((await environment()).password);
  await page.getByRole('button', {name: 'Sign in', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Welcome, Fictional New Member'})).toBeVisible();
  await page.getByRole('link', {name: 'My security', exact: true}).click();
  await page.getByRole('button', {name: 'Set up authenticator'}).click();
  const seed = await page.getByTestId('setup-seed').innerText();
  await page.getByLabel('New authenticator code', {exact: true}).fill(await control('totp', {seed}));
  await page.getByRole('button', {name: 'Confirm authenticator'}).click();
  const codes = page.getByRole('dialog', {name: 'Save your recovery codes'});
  await expect(codes).toBeVisible(); await expect(codes.locator('li')).toHaveCount(10);
  await codes.getByRole('button', {name: 'I saved these codes'}).click();
  await expect(page.getByTestId('setup-seed')).toHaveCount(0);
  await expect(page.getByText('Authenticator enrolled', {exact: true})).toBeVisible();
  await noStoredCredentials(page);
});

test('password reset is browser-bound and preserves MFA', async ({page}) => {
  await open(page, '/console/reset-request');
  await page.getByLabel('Email address', {exact: true}).fill('member@example.test');
  await page.getByRole('button', {name: 'Request reset'}).click();
  await expect(page.getByText('If eligible, a reset message will be delivered.')).toBeVisible();
  await page.getByRole('link', {name: 'I have a reset code'}).click();
  await page.getByLabel('Reset code', {exact: true}).fill(await control('mail', {purpose: 'reset', email: 'member@example.test'}));
  await page.getByLabel('New password', {exact: true}).fill('fictional-new-reset-password');
  await page.getByRole('button', {name: 'Reset password', exact: true}).click();
  await expect(page.getByText('Password reset accepted. Sign in with your new password and existing MFA.')).toBeVisible();
  expect(await control<boolean>('factor-active')).toBe(true);
  await localLogin(page, 'member', 0, 'fictional-new-reset-password');
  await noStoredCredentials(page);
});

test('real Keycloak sign-in, step-up boundary, provider logout and callback refusal', async ({page}) => {
  await open(page);
  await page.getByLabel('Organization ID', {exact: true}).fill('org-a');
  await page.getByRole('button', {name: 'Continue with SSO'}).click();
  await providerLogin(page);
  await expect(page.getByRole('heading', {name: 'Welcome, member'})).toBeVisible();
  await page.getByRole('link', {name: 'My security', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Change password'})).toBeDisabled();
  await expect(page.getByRole('button', {name: 'Reauthenticate with provider'})).toBeVisible();
  await page.route('**/api/v6/auth/oidc/reauthenticate', route => route.abort('failed'), {times: 1});
  await page.getByRole('button', {name: 'Reauthenticate with provider'}).click();
  await expect(page.getByText('The service could not be reached. Try again when the connection is available.')).toBeVisible();
  await expect(page.getByText('Organization SSO', {exact: true})).toBeVisible();
  await page.getByLabel('Use a recovery code', {exact: true}).check();
  await page.getByLabel('Authentication or recovery code', {exact: true}).fill(await control('recovery'));
  await page.getByRole('button', {name: 'Verify identity', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Change password'})).toBeEnabled();
  await expect(page.getByText('Organization SSO', {exact: true})).toBeVisible();
  await noStoredCredentials(page);
  await page.getByRole('button', {name: 'Sign out', exact: true}).click();
  await page.getByRole('button', {name: 'Logout', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Your context, connected.'})).toBeVisible();
  await expect(page.getByText('You signed out of the provider and this console.')).toBeVisible();
  await open(page, '/api/v6/auth/oidc/callback?state=invalid&code=invalid');
  await expect(page.getByText(/Provider sign-in was not accepted/)).toBeVisible();
  expect(page.url()).not.toMatch(/code=|state=/);
});
