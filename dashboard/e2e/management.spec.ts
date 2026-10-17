import AxeBuilder from '@axe-core/playwright';
import { environment, expect, localLogin, noStoredCredentials, test } from './fixture';
import { api, authoring, goto, include, tuple } from './management-fixture';

test('owner assigns bounded lead authority; lead invites a worker; independent request decision and offboarding', async ({page, browser}, info) => {
  await localLogin(page, 'owner');
  const ownerContext = await authoring(page); const root = ownerContext.parents.find(p => p.remaining_depth > 1); expect(root).toBeTruthy();
  await goto(page, 'people/lead');
  await page.getByRole('button', {name: 'Assign bounded policy'}).click();
  await page.getByLabel('Reviewed parent authority').selectOption(root!._id);
  await page.getByLabel('Human role', {exact: true}).selectOption('team_lead');
  await page.getByLabel('Team scope').selectOption('alpha');
  for (const action of ['members:read', 'members:invite', 'members:suspend', 'members:reinstate', 'grants:read', 'grants:delegate', 'teams:read', 'clients:manage:team', 'clients:read:team', 'credentials:revoke:team']) await tuple(page, 'Personal use', include(action, 'team', ['alpha']));
  await page.getByText('Onward delegation envelope (defaults to NONE)', {exact: true}).click();
  await tuple(page, 'Delegation ceiling', include('context:read'));
  await page.getByLabel('Allowed onward human roles (empty means NONE)').fill('contributor');
  await page.getByLabel('Remaining delegation depth').fill('1');
  await page.getByRole('button', {name: 'Review policy', exact: true}).click();
  await expect(page.getByRole('dialog', {name: 'Review bounded policy'})).toBeVisible(); await page.screenshot({path: info.outputPath('bounded-lead-preview.png'), fullPage: true});
  await page.getByRole('button', {name: 'Confirm assignment'}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  const leadContext = await browser.newContext({ignoreHTTPSErrors: true}); const workerContext = await browser.newContext({ignoreHTTPSErrors: true});
  try {
    const lead = await leadContext.newPage(); await localLogin(lead, 'lead');
    await goto(lead, 'invitations'); await lead.getByRole('button', {name: 'Invite a person', exact: true}).click();
    const policy = await authoring(lead); const parent = policy.parents.find(p => p.remaining_depth > 0 && p.ceiling.some(g => g.action === 'context:read') && p.tuples.some(g => g.action === 'members:invite'))!;
    await lead.getByLabel('Recipient email').fill('member@example.test'); await lead.getByLabel('Display name').fill('Fictional Worker');
    await lead.getByLabel('Reviewed parent authority').selectOption(parent._id);
    await tuple(lead, 'Personal use', include('context:read'));
    await lead.getByRole('button', {name: 'Review policy', exact: true}).click(); await lead.getByRole('button', {name: 'Confirm invitation'}).click();
    await expect(lead.getByRole('dialog')).toHaveCount(0);
    await expect(lead.getByRole('table')).toContainText('member');
    const worker = await workerContext.newPage(); await localLogin(worker, 'member');
    // The real test mail sink delivers a one-use token; no product test endpoint.
    const {control} = await import('./fixture'); const token = await control('mail', {purpose: 'management_invite', email: 'member@example.test'});
    await goto(worker, 'my-invitations'); await worker.getByLabel('Delivered invitation secret').fill(token); await worker.getByRole('button', {name: 'Review invitation acceptance'}).click(); await worker.getByRole('button', {name: 'Confirm invitation acceptance'}).click(); await expect(worker.getByRole('dialog')).toHaveCount(0);
    await goto(worker, 'access-requests'); await worker.getByRole('button', {name: 'Request access', exact: true}).click();
    const own = await authoring(worker); const requestParent = own.request_parents.find(p => p.id === parent._id)!;
    await worker.getByLabel('Reviewed parent authority').selectOption(requestParent.id);
    await tuple(worker, 'Personal use', include('context:read'));
    await worker.getByRole('button', {name: 'Review policy', exact: true}).click(); await worker.getByRole('button', {name: 'Confirm access request'}).click();
    await expect(worker.getByRole('dialog')).toHaveCount(0);
    await goto(lead, 'access-requests'); await lead.getByRole('button', {name: 'Approve request', exact: true}).click();
    await lead.getByRole('button', {name: 'Confirm approve request'}).click();
    await expect(lead.getByRole('table')).toContainText('approved');
    const foreign = await api(lead, '/organizations/org-a/management/members/other'); expect(foreign.status()).toBe(403);
    await goto(lead, 'people/member'); await lead.getByRole('button', {name: 'Suspend membership', exact: true}).click(); await lead.getByRole('button', {name: 'Confirm suspend membership'}).click();
    await expect(lead.getByRole('heading', {name: 'member', exact: true})).toBeVisible();
    await goto(worker, 'workspace'); await expect(worker.getByRole('heading', {name: 'Your context, connected.'})).toBeVisible();
    await noStoredCredentials(lead);
  } finally {await leadContext.close(); await workerContext.close();}
});

test('foreign authority, self elevation and stale decisions are refused through direct session API calls', async ({page}) => {
  await localLogin(page, 'lead'); const context = await authoring(page); const parent = context.parents.find(p => p.remaining_depth > 0)!;
  const body = {subject_id: 'lead', role: 'org_owner', parent_id: parent._id, expected_parent_generation: parent.generation, tuples: [], ceiling: [], targets: {mode: 'none', ids: []}, allowed_roles: [], remaining_depth: 0, expires_at: new Date(Date.now() + 3600000).toISOString(), team_id: 'alpha', expected_team_generation: 1};
  expect((await api(page, '/organizations/org-a/delegations', body)).status()).toBe(403);
  expect((await api(page, '/organizations/org-b/management/context')).status()).toBe(403);
  expect((await api(page, '/organizations/org-a/delegations', {...body, subject_id: 'other', role: 'contributor'})).status()).toBe(403);
  expect((await api(page, '/organizations/org-a/access-requests/foreign/decisions', {expected_revision: 1, expected_policy_revision: 1, outcome: 'approved'})).status()).toBe(403);
  await noStoredCredentials(page);
});

test('real management tables and bounded editor reflow, focus and themes', async ({page}, info) => {
  await localLogin(page, 'owner');
  for (const section of ['people', 'teams', 'access-requests', 'agent-templates']) {
    await goto(page, section); await expect(page.getByRole('heading', {name: ({people: 'People', teams: 'Teams', 'access-requests': 'Access requests', 'agent-templates': 'Agent permission templates'} as Record<string, string>)[section] ?? section, exact: true})).toBeVisible(); await expect(page.locator('.skeleton')).toHaveCount(0);
    for (const width of [360, 768, 1440]) for (const theme of ['light', 'dark']) {
      await page.setViewportSize({width, height: 1000}); await page.evaluate(t => {document.documentElement.dataset.theme = t;}, theme);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect((await new AxeBuilder({page}).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([]);
      await page.screenshot({path: info.outputPath(`${section}-${width}-${theme}.png`), fullPage: true});
    }
  }
  await goto(page, 'people'); await page.getByRole('button', {name: 'Invite a person'}).focus(); await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible(); await page.keyboard.press('Tab');
  expect(await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))).toBe(true);
  await page.keyboard.press('Escape'); await expect(page.getByRole('button', {name: 'Invite a person'})).toBeFocused();
  const cdp = await page.context().newCDPSession(page); await cdp.send('Emulation.setDeviceMetricsOverride', {width: 720, height: 500, deviceScaleFactor: 2, mobile: false});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); await page.screenshot({path: info.outputPath('management-200-percent-equivalent.png'), fullPage: true}); await cdp.detach();
  expect((await environment()).origin).toMatch(/^https:\/\/127\.0\.0\.1:/);
});

test('real team, service custody, project-binding and ownership-offer lifecycle', async ({page}, info) => {
  await localLogin(page, 'owner');
  await goto(page, 'teams'); await page.getByRole('button', {name: 'Create team', exact: true}).click();
  await page.getByLabel('Team ID', {exact: true}).fill('gamma'); await page.getByLabel('Team name', {exact: true}).fill('Fictional Gamma');
  await page.getByRole('dialog').getByRole('button', {name: 'Create team', exact: true}).click(); await expect(page.getByRole('dialog')).toHaveCount(0);
  await goto(page, 'teams/gamma'); await page.getByLabel('New team name').fill('Fictional Gamma Reviewed'); await page.getByRole('button', {name: 'Rename team', exact: true}).click(); await page.getByRole('button', {name: 'Confirm rename team'}).click();
  await expect(page.getByRole('heading', {name: 'Fictional Gamma Reviewed', exact: true})).toBeVisible();
  await page.getByLabel('Verified member ID to add').fill('other'); await page.getByRole('button', {name: 'Add team member'}).click(); await expect(page.getByRole('table')).toContainText('other');
  await goto(page, 'service-accounts'); await page.getByRole('button', {name: 'Approve service custody'}).click();
  await page.getByLabel('Service-account ID', {exact: true}).fill('fictional-ci'); await page.getByLabel('Service-account name').fill('Fictional CI'); await page.getByLabel('Verified custodian member ID').fill('member'); await page.getByLabel('Custody team').selectOption('alpha');
  await page.getByRole('button', {name: 'Confirm independent custody approval'}).click(); await expect(page.getByRole('dialog')).toHaveCount(0); await expect(page.getByRole('table')).toContainText('fictional-ci'); await page.screenshot({path: info.outputPath('service-custody.png'), fullPage: true});
  await page.getByRole('button', {name: 'Revoke service custody', exact: true}).click(); await page.getByRole('button', {name: 'Confirm revoke service custody'}).click(); await expect(page.getByRole('table')).toContainText('revoked');
  await goto(page, 'bindings'); await page.getByRole('button', {name: 'Create project binding'}).click();
  await page.getByLabel('Binding ID', {exact: true}).fill('fictional-workspace'); await page.getByLabel('Project ID', {exact: true}).fill('payment-service'); await page.getByLabel('Exact mapping value').fill('fictional-workspace-alias');
  await page.getByRole('button', {name: 'Review binding', exact: true}).click(); await page.getByRole('button', {name: 'Confirm binding change'}).click(); await expect(page.getByRole('dialog')).toHaveCount(0);
  const row = page.getByRole('row').filter({hasText: 'fictional-workspace-alias'}); await expect(row).toBeVisible(); await page.screenshot({path: info.outputPath('project-binding.png'), fullPage: true}); await row.getByRole('button', {name: 'Delete binding', exact: true}).click(); await page.getByRole('button', {name: 'Confirm delete binding'}).click(); await expect(page.getByText('fictional-workspace-alias', {exact: true})).toHaveCount(0);
  await goto(page, 'people/other'); await page.getByRole('button', {name: 'Offer ownership transfer', exact: true}).click(); await page.getByRole('button', {name: 'Confirm offer ownership transfer'}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0); await goto(page, 'ownership'); await expect(page.getByRole('table')).toContainText('owner → other'); await expect(page.getByRole('button', {name: 'Accept ownership', exact: true})).toBeDisabled(); await page.screenshot({path: info.outputPath('ownership-offer.png'), fullPage: true});
});
