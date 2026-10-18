import { control, expect, localLogin, noStoredCredentials, test } from './fixture';
import { api, authoring, goto, include, tuple } from './management-fixture';

test('template copy, lead approval, recipient one-time key, rotation and offboarding use real APIs', async ({page, browser}, info) => {
  await localLogin(page, 'owner'); await goto(page, 'agent-templates');
  await page.getByRole('button', {name: 'Create agent template'}).click();
  await page.getByLabel('Agent permission-template ID').fill('fictional-coding'); await page.getByLabel('Template name').fill('Fictional Coding'); await page.getByLabel('Template scope ceiling').fill('context:read');
  await page.getByRole('button', {name: 'Review template impact'}).click(); await page.getByRole('button', {name: 'Confirm template change'}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  const machineContext = await browser.newContext({ignoreHTTPSErrors: true}); const leadContext = await browser.newContext({ignoreHTTPSErrors: true}); const workerContext = await browser.newContext({ignoreHTTPSErrors: true, permissions: ['clipboard-read', 'clipboard-write']});
  try {
    const lead = await leadContext.newPage(); await localLogin(lead, 'lead'); await goto(lead, 'clients');
    await lead.getByRole('button', {name: 'Approve machine client'}).click();
    await lead.getByLabel('Machine client ID').fill('fictional-worker'); await lead.getByLabel('Machine client name').fill('Fictional worker'); await lead.getByLabel('Verified recipient member ID').fill('member');
    const context = await authoring(lead); const parent = context.parents.find(p => p.remaining_depth > 0 && p.ceiling.some(g => g.action === 'context:read'))!;
    await lead.getByLabel('Client parent authority').selectOption(parent._id);
    await lead.getByLabel('Agent permission template (copy on assignment)').selectOption('fictional-coding');
    await tuple(lead, 'Client grant', include('context:read'));
    await lead.getByRole('button', {name: 'Preview effective client grant'}).click();
    await expect(lead.getByRole('heading', {name: 'Current effective capabilities'})).toBeVisible(); const layers = await lead.locator('.modal-overlay').evaluateAll(nodes => nodes.map(node => Number(getComputedStyle(node).zIndex))); expect(layers[1]).toBeGreaterThan(layers[0]!); await lead.screenshot({path: info.outputPath('client-bounded-preview.png'), fullPage: true});
    await lead.getByRole('button', {name: 'Confirm client policy approval'}).click(); await expect(lead.getByRole('dialog')).toHaveCount(0);
    await goto(lead, 'clients/fictional-worker'); await expect(lead.getByRole('button', {name: 'Generate my machine key'})).toBeDisabled();
    const forbidden = await api(lead, '/organizations/org-a/managed-clients/fictional-worker/credentials', {issuance_id: 'not-recipient', expected_client_revision: 1, tuples: [include('context:read')], expires_at: new Date(Date.now() + 3600000).toISOString()});
    expect(forbidden.status()).toBe(403); expect(await forbidden.text()).not.toContain('token');
    // Disable the template through its real impact preview; the copied grant stays.
    await goto(page, 'agent-templates'); await page.getByRole('row').filter({hasText: 'fictional-coding'}).getByRole('button', {name: 'Edit template / impact'}).click();
    await page.getByLabel('Template status').selectOption('disabled'); await page.getByRole('button', {name: 'Review template impact'}).click();
    await expect(page.getByText(/1 assigned clients retain their existing copied grants/)).toBeVisible();
    await page.getByRole('button', {name: 'Confirm template change'}).click(); await expect(page.getByRole('dialog')).toHaveCount(0);
    const inspection = await (await api(lead, '/organizations/org-a/managed-clients/fictional-worker')).json(); expect(inspection.client.scopes).toEqual(['context:read']); expect(inspection.client.profile_revision).toBe(1); expect(inspection.client.revision).toBe(1);
    const worker = await workerContext.newPage(); await localLogin(worker, 'member'); await goto(worker, 'clients/fictional-worker');
    await worker.evaluate(async () => navigator.clipboard.writeText('synthetic-clipboard-before-explicit-copy'));
    await worker.getByRole('button', {name: 'Generate my machine key'}).click();
    expect(await worker.getByText('NONE — no grants selected.').count()).toBeGreaterThan(0);
    await tuple(worker, 'Credential grant', include('context:read'));
    await worker.getByRole('button', {name: 'Review credential'}).click();
    const delivered = worker.waitForResponse(r => r.url().endsWith('/fictional-worker/credentials') && r.request().method() === 'POST');
    await worker.getByRole('button', {name: 'Confirm one-time generation'}).click(); const response = await delivered;
    expect(response.status()).toBe(200); expect(response.headers()['cache-control']).toBe('no-store');
    const receipt = await response.json(); const secret = receipt.token as string;
    await expect(worker.getByLabel('One-time machine key', {exact: true})).toHaveText(secret); await worker.screenshot({path: info.outputPath('synthetic-recipient-once.png'), fullPage: true});
    expect(await worker.evaluate(async () => navigator.clipboard.readText())).toBe('synthetic-clipboard-before-explicit-copy');
    await worker.getByRole('button', {name: 'Copy machine key'}).click(); expect(await worker.evaluate(async () => navigator.clipboard.readText())).toBe(secret);
    await noStoredCredentials(worker);
    const retry = await api(worker, '/organizations/org-a/managed-clients/fictional-worker/credentials', response.request().postDataJSON());
    expect(retry.ok()).toBe(true); expect((await retry.json()).delivered).toBe(false); expect(await retry.text()).not.toContain(secret);
    await worker.getByRole('button', {name: 'I saved it — clear key'}).click(); await expect(worker.getByLabel('One-time machine key', {exact: true})).toHaveCount(0);
    // Synthetic credential may authenticate only its reviewed domain action.
    const {environment} = await import('./fixture'); const origin = (await environment()).origin;
    const bearer = await machineContext.request.get(origin + '/api/v1/context?project_id=payment-service', {headers: {Authorization: 'Bearer ' + secret}});
    expect(bearer.status()).toBe(200);
    await worker.getByRole('button', {name: 'Rotate my key'}).click(); await tuple(worker, 'Credential grant', include('context:read')); await worker.getByRole('button', {name: 'Review credential'}).click();
    await worker.getByRole('button', {name: 'Confirm one-time generation'}).click(); await expect(worker.getByLabel('One-time machine key', {exact: true})).toBeVisible();
    expect(await worker.getByLabel('One-time machine key', {exact: true}).textContent()).not.toBe(secret);
    await worker.getByRole('button', {name: 'I saved it — clear key'}).click();
    const old = await machineContext.request.get(origin + '/api/v1/context?project_id=payment-service', {headers: {Authorization: 'Bearer ' + secret}}); expect([401, 403]).toContain(old.status());
    await goto(lead, 'clients/fictional-worker'); await lead.getByRole('button', {name: 'Revoke client', exact: true}).click(); await lead.getByRole('button', {name: 'Confirm revoke client'}).click();
    await goto(worker, 'clients/fictional-worker'); await expect(worker.getByRole('button', {name: 'Generate my machine key'})).toBeDisabled();
    await noStoredCredentials(worker);
    await worker.evaluate(async () => navigator.clipboard.writeText(''));
  } finally {await leadContext.close(); await workerContext.close(); await machineContext.close();}
});

test('stale revisions and revoked sponsorship refuse client/session work even if controls are unhidden', async ({page}) => {
  await localLogin(page, 'lead');
  expect((await api(page, '/organizations/org-a/managed-clients/foreign/revoke', {expected_revision: 1})).status()).toBe(403);
  expect((await api(page, '/organizations/org-a/management/clients/foreign/sessions/foreign/end', {expected_revision: 1})).status()).toBe(403);
  await control('stale-mfa', {user: 'lead'}); await goto(page, 'clients');
  await expect(page.getByText(/Recent MFA is required for changes/)).toBeVisible();
  const context = await authoring(page); const parent = context.parents.find(p => p.remaining_depth > 0)!;
  const denied = await api(page, '/organizations/org-a/managed-clients/preview', {client_id: 'unhidden', name: 'Fictional', subject_id: 'member', parent_id: parent._id, expected_parent_generation: parent.generation, team_id: 'alpha', expected_team_generation: 1, tuples: [include('context:read')], expires_at: new Date(Date.now() + 3600000).toISOString()});
  expect(denied.status()).toBe(401);
});
