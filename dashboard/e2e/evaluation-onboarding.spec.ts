import { expect, localLogin, noStoredCredentials, test } from './fixture';
import { api, authoring, goto, include } from './management-fixture';

test('connection preview preserves policy and guides recipient diagnostics', async ({page}, info) => {
  await localLogin(page, 'owner');
  const context = await authoring(page);
  const parent = context.parents.find(p => p.remaining_depth > 0 && p.ceiling.some(g => g.action === 'context:read'))!;
  const approved = await api(page, '/organizations/org-a/managed-clients', {client_id: 'connection-worker', name: 'Fictional connection preview', client_type: 'codex', subject_id: 'member', parent_id: parent._id, expected_parent_generation: parent.generation, tuples: [include('context:read')], expires_at: new Date(Date.now() + 3600000).toISOString()});
  expect(approved.status()).toBe(201);
  // This is the real existing managed-client fixture, not a fresh installer claim.
  await goto(page, 'clients/connection-worker');
  const before = await (await api(page, '/organizations/org-a/managed-clients/connection-worker')).json();
  await page.getByLabel('Connection client').selectOption('codex');
  await page.getByLabel('Explicit connection project ID').fill('payment-service');
  await page.getByLabel('Require server-approved read-only access').check();
  await page.getByRole('button', {name: 'Preview connection recipe'}).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByLabel('Connection doctor command')).toContainText('--require-read-only');
  await expect(page.getByLabel('Connection configuration')).toContainText('MCP_API_CA_FILE');
  await expect(page.getByText('transport: not checked in preview', {exact: true})).toBeVisible();
  const foreign = await api(page, '/organizations/org-b/managed-clients/connection-worker/connection-preview?integration_id=codex&origin=https%3A%2F%2Fexample.test&project_id=payment-service');
  expect([403, 409]).toContain(foreign.status());
  expect(await foreign.text()).not.toContain('doctor_command');
  const after = await (await api(page, '/organizations/org-a/managed-clients/connection-worker')).json();
  expect(after).toEqual(before);
  expect(await page.getByLabel('Connection recipe').innerText()).not.toContain('acp_');
  expect(await page.getByLabel('Bearer token').count()).toBe(0);
  // Keyboard access to long code blocks, real responsive layout and both themes.
  await page.getByLabel('Connection configuration').focus();
  await expect(page.getByLabel('Connection configuration')).toBeFocused();
  for (const theme of ['light', 'dark'] as const) {
    if (await page.evaluate(() => document.documentElement.dataset.theme) !== theme) {
      await page.getByRole('button', {name: theme === 'dark' ? 'Use dark theme' : 'Use light theme'}).click();
    }
    expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe(theme);
    for (const width of [360, 768, 1440]) {
      await page.setViewportSize({width, height: 1000});
      await expect(page.getByRole('heading', {name: 'Connect this client'})).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.getByLabel('Connection recipe').screenshot({path: info.outputPath(`connection-${theme}-${width}.png`)});
    }
  }
  await noStoredCredentials(page);
});
