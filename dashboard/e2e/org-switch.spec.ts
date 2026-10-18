import { control, environment, expect, localLogin, noStoredCredentials, open, providerLogin, test } from './fixture';

test('target SSO provider is explicit; org switch rejects late responses and Browser Back', async ({page}) => {
  await open(page);
  await page.getByLabel('Organization ID', {exact: true}).fill('org-a');
  await page.getByRole('button', {name: 'Continue with SSO'}).click();
  await providerLogin(page);
  await expect(page.getByRole('heading', {name: 'Welcome, member'})).toBeVisible();
  let release = () => {}; let received = () => {};
  const arrived = new Promise<void>(resolve => {received = resolve;});
  const held = new Promise<void>(resolve => {release = resolve;});
  await page.route('**/api/v6/me/effective-access', async route => {
    const response = await route.fetch(); // actual authorized API response, delayed in transport
    received(); await held;
    await route.fulfill({response}).catch(() => {});
  }, {times: 1});
  await page.getByRole('link', {name: 'My access', exact: true}).click();
  await arrived;
  const before = await page.context().cookies((await environment()).origin);
  await page.getByLabel('Active organization').selectOption('org-b');
  await expect(page.getByRole('dialog', {name: 'Organization sign-in required'})).toBeVisible();
  release();
  await page.getByRole('button', {name: 'Continue to approved sign-in'}).click();
  await providerLogin(page);
  await expect(page.getByLabel('Active organization')).toHaveValue('org-b');
  await expect(page.getByText('Approved SSO required', {exact: true})).toBeVisible();
  const after = await page.context().cookies((await environment()).origin);
  expect(after.find(cookie => cookie.name === '__Host-acp-session')?.value).not.toBe(before.find(cookie => cookie.name === '__Host-acp-session')?.value);
  await page.goBack();
  // Browser Back can replay the already-consumed OIDC callback. Fail closed,
  // then explicitly recover the still-current session without restoring old DOM.
  await expect(page.getByText(/Provider sign-in was not accepted/)).toBeVisible();
  await expect(page.getByText('Fictional Alpha', {exact: true})).toHaveCount(0);
  await page.getByRole('button', {name: 'Check current session'}).click();
  await expect(page.getByLabel('Active organization')).toHaveValue('org-b');
  await expect(page.getByRole('heading', {name: 'Welcome, member'})).toBeVisible();
  await noStoredCredentials(page);
});

test('unsaved sensitive forms are either retained in their org or explicitly discarded', async ({page}) => {
  await localLogin(page);
  await page.getByRole('link', {name: 'My security', exact: true}).click();
  await page.getByLabel('Current password', {exact: true}).fill('fictional-sensitive-draft');
  await page.getByLabel('Active organization').selectOption('org-b');
  await page.getByRole('button', {name: 'Keep editing'}).click();
  await expect(page.getByLabel('Current password', {exact: true})).toHaveValue('fictional-sensitive-draft');
  await page.getByLabel('Active organization').selectOption('org-b');
  await page.getByRole('button', {name: 'Discard and switch'}).click();
  await expect(page.getByRole('dialog', {name: 'Organization sign-in required'})).toBeVisible();
  await expect(page.getByLabel('Current password', {exact: true})).toHaveValue('');
  await page.getByRole('button', {name: 'Stay in current organization'}).click();
  await expect(page.getByLabel('Active organization')).toHaveValue('org-a');
});

test('expired persisted sessions clear private views and recover a usable preauth login', async ({page}) => {
  await localLogin(page);
  await control('expire');
  await page.getByRole('link', {name: 'My access', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Your context, connected.'})).toBeVisible();
  await expect(page.getByText('Your session ended. Sign in again to continue.')).toBeVisible();
  await localLogin(page, 'member', 1);
});


test('direct organization switch fences an actual delayed read and old SPA history', async ({page}) => {
  await control('prefer-b');
  await localLogin(page);
  const origin = (await environment()).origin;
  const csrfBefore: unknown = (await (await page.request.get(origin + '/api/v6/auth/csrf')).json()).csrf_token;
  let release = () => {}; let received = () => {};
  const arrived = new Promise<void>(resolve => {received = resolve;});
  const held = new Promise<void>(resolve => {release = resolve;});
  await page.route('**/api/v6/me/effective-access', async route => {
    const response = await route.fetch(); received(); await held;
    await route.fulfill({response}).catch(() => {});
  }, {times: 1});
  await page.getByRole('link', {name: 'My access', exact: true}).click(); await arrived;
  await page.getByLabel('Active organization').selectOption('org-b');
  await expect(page.getByLabel('Active organization')).toHaveValue('org-b');
  expect((await (await page.request.get(origin + '/api/v6/auth/csrf')).json()).csrf_token).not.toBe(csrfBefore);
  release();
  await page.goBack();
  await expect(page.getByLabel('Active organization')).toHaveValue('org-b');
  await expect(page.getByRole('heading', {name: 'Welcome, member'})).toBeVisible();
  expect(page.url()).toContain('/o/org-b/workspace');
  await page.getByRole('link', {name: 'My access', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'No effective resource assignments'})).toBeVisible();
  await expect(page.getByText('context:write', {exact: true})).toHaveCount(0);
});


test('unavailable bootstrap hides private views until explicit reconnection', async ({page}) => {
  await localLogin(page);
  const origin = (await environment()).origin;
  const cookie = (await page.context().cookies(origin)).find(value => value.name === '__Host-acp-session')?.value;
  await page.route('**/api/v6/me/console', route => route.abort('failed'), {times: 1});
  await page.reload();
  await expect(page.getByRole('heading', {name: 'Session verification unavailable'})).toBeVisible();
  await expect(page.getByLabel('Active organization')).toHaveCount(0);
  await expect(page.getByLabel('Password', {exact: true})).toHaveCount(0);
  await page.getByRole('button', {name: 'Retry connection'}).click();
  await expect(page.getByRole('heading', {name: 'Welcome, member'})).toBeVisible();
  expect((await page.context().cookies(origin)).find(value => value.name === '__Host-acp-session')?.value).toBe(cookie);
  await noStoredCredentials(page);
});
