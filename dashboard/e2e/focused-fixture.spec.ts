import { test, expect, environment, control } from './fixture';

test('focused fixture serves only the selected browser check', async ({page, request}) => {
  // A runner/lifecycle check, not a claim of dashboard or vendor compatibility.
  // Node trusts only the fresh fixture CA through NODE_EXTRA_CA_CERTS.
  const {origin} = await environment();
  const csrf = await request.get(origin + '/api/v6/auth/csrf');
  expect(csrf.status()).toBe(200);
  expect((await csrf.json()).csrf_token).toBeTruthy();
  expect((await request.get(origin + '/api/v6/me')).status()).toBe(401);
  expect(await control('recovery', {user: 'member', index: 0})).toBeTruthy();
  await page.goto('about:blank');
  expect(await page.evaluate(() => location.href)).toBe('about:blank');
});
