import { control, environment, expect, localLogin, noStoredCredentials, open, providerLogin, test } from './fixture';

test('a deferred screen asset failure preserves context and offers a safe reload', async ({page}) => {
  await localLogin(page, 'owner');
  await page.route('**/assets/People-*.js', route => route.abort('failed'));
  await page.getByRole('link', {name: 'People', exact: true}).click();
  await expect(page.getByText('This workspace could not load.', {exact: false})).toBeVisible();
  await expect(page.getByRole('region', {name: 'Current organization context'})).toContainText('Fictional Alpha');
  await expect(page.getByRole('main')).toHaveCount(1);
  await noStoredCredentials(page);
  await page.unroute('**/assets/People-*.js');
  await page.getByRole('button', {name: 'Reload workspace', exact: true}).click();
  await expect(page.getByRole('table', {name: 'Organization people'})).toBeVisible();
});

test('distribution gateway serves only console routes and real API/MCP/asset errors over TLS', async ({page}) => {
  await open(page, '/console/sign-in');
  const env = await environment();
  const response = await page.request.get(env.origin + '/console/people');
  expect(response.status()).toBe(200);
  expect(response.headers()['content-security-policy']).toContain("frame-ancestors 'none'");
  expect(response.headers()['x-frame-options']).toBe('DENY');
  expect(response.headers()['cache-control']).toBe('no-store');
  for (const path of ['/console/assets/does-not-exist.js', '/console/licenses/missing-LICENSE', '/console/missing.svg', '/api/v6/no-such-route', '/backups/archive.gz', '/metrics']) {
    const missing = await page.request.get(env.origin + path);
    expect(missing.status()).toBe(404); expect(missing.headers()['cache-control']).toBe('no-store');
    expect(await missing.text()).not.toContain('<div id="root">');
  }
  const mcp = await page.request.post(env.origin + '/mcp', {data: {}});
  expect(mcp.status()).toBe(401); expect(mcp.headers()['cache-control']).toBe('no-store');
  const asset = page.locator('script[src]').first();
  const js = await page.request.get(env.origin + await asset.getAttribute('src'));
  expect(js.status()).toBe(200); expect(js.headers()['cache-control']).toContain('immutable');
  expect(await js.text()).not.toContain(env.password);
  await noStoredCredentials(page);
});

test('production images support local password/MFA, session-only boundary and logout', async ({page}) => {
  await control('local-only');
  await localLogin(page);
  await noStoredCredentials(page);
  const env = await environment();
  expect((await page.request.get(env.origin + '/api/v1/projects')).status()).toBe(401);
  expect((await page.request.get(env.origin + '/mcp')).status()).toBe(401);
  expect((await page.request.post(env.origin + '/api/v6/auth/logout', {data: {}, headers: {Origin: env.origin}})).status()).toBe(403);
  await page.getByRole('button', {name: 'Sign out', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Your context, connected.'})).toBeVisible();
  expect((await page.request.get(env.origin + '/api/v6/me')).status()).toBe(401);
});

test('production-mode Keycloak with PostgreSQL completes exact redirect login and logout', async ({page}) => {
  await open(page);
  await page.getByLabel('Organization ID', {exact: true}).fill('org-a');
  await page.getByRole('button', {name: 'Continue with SSO'}).click();
  await providerLogin(page);
  await expect(page.getByRole('heading', {name: 'Welcome, member'})).toBeVisible();
  await noStoredCredentials(page);
  await page.getByRole('button', {name: 'Sign out', exact: true}).click();
  await page.getByRole('button', {name: 'Logout', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Your context, connected.'})).toBeVisible();
  await expect(page.getByText('You signed out of the provider and this console.')).toBeVisible();
});
