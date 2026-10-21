import { control, environment, expect, localLogin, noStoredCredentials, test } from './fixture';
import { api, authoring, goto } from './management-fixture';
import { prepare as prepareMonitoring, query as monitoringQuery } from './monitoring-fixture';

test('host cookies cannot cross bearer surfaces and every session mutation checks Origin and CSRF', async ({page}) => {
  await localLogin(page);
  const origin = (await environment()).origin;
  const csrf = await (await page.request.get(origin + '/api/v6/auth/csrf')).json();
  for (const headers of [
    {Origin: 'https://foreign.invalid', 'X-CSRF-Token': csrf.csrf_token as string},
    {Origin: origin, 'X-CSRF-Token': 'forged-csrf'},
    {Origin: origin},
  ]) {
    const refused = await page.request.post(origin + '/api/v6/auth/logout', {headers, data: {}});
    expect(refused.status()).toBe(403);
    expect(refused.headers()['cache-control']).toBe('no-store');
    expect((await page.request.get(origin + '/api/v6/me')).ok()).toBe(true);
  }
  expect((await page.request.get(origin + '/api/v1/projects')).status()).toBe(401);
  const mcp = await page.request.post(origin + '/mcp', {headers: {Origin: origin,
    Accept: 'application/json, text/event-stream'}, data: {jsonrpc: '2.0', id: 1,
    method: 'initialize', params: {protocolVersion: '2025-06-18', capabilities: {},
      clientInfo: {name: 'synthetic-browser-boundary', version: '1'}}}});
  expect(mcp.status()).toBe(401);
  await noStoredCredentials(page);
});

test('unhidden management calls cannot self-grant or inspect a foreign organization', async ({page}) => {
  await localLogin(page);
  const window = new URLSearchParams({source: 'governance', scope: 'org', start: new Date(Date.now() - 3600000).toISOString(), end: new Date(Date.now() - 1000).toISOString()});
  for (const path of ['/organizations/org-b/management/context',
    '/organizations/org-a/activity?' + window.toString()]) {
    const response = await api(page, path);
    expect(response.status(), path).toBe(403);
    expect(await response.text()).not.toMatch(/PRIVATE_SENTINEL|key_hash|password_hash/);
  }
  const directory = await api(page, '/organizations/org-a/members');
  expect(directory.status()).toBe(200);
  const empty = await directory.json(); expect(empty.items).toEqual([]); expect(empty.next_cursor).toBeNull();
  const context = await authoring(page); const parent = context.parents[0]!;
  expect(parent.remaining_depth).toBe(0);
  expect((await api(page, '/organizations/org-a/delegations', {
    subject_id: 'member', role: 'contributor', parent_id: parent._id,
    expected_parent_generation: parent.generation,
    tuples: [{action: 'context:write', resource_kind: 'project', selector: 'include', ids: ['payment-service']}],
    expires_at: new Date(Date.now() + 3600000).toISOString(),
  })).status()).toBe(403);
  await noStoredCredentials(page);
});

test('stored hostile Markdown remains inert under the production CSP and real search policy', async ({page}) => {
  test.setTimeout(240000);
  await control('knowledge');
  await localLogin(page);
  await goto(page, 'knowledge/search?project=payment-service');
  await page.getByLabel('Search terms', {exact: true}).fill('isolation');
  await page.getByLabel('Search source').selectOption('project_context');
  const result = page.waitForResponse(r => r.url().endsWith('/knowledge/search') && r.request().method() === 'POST');
  await page.getByRole('button', {name: 'Search knowledge', exact: true}).click();
  const response = await result;
  expect(response.ok()).toBe(true);
  expect(JSON.stringify(await response.json())).not.toMatch(/PRIVATE_SENTINEL_B|PRIVATE_AGENT_SENTINEL/);
  await expect(page.locator('.knowledge-markdown strong').first()).toHaveText('Safe bold');
  await expect(page.locator('.knowledge-markdown script, .knowledge-markdown iframe, .knowledge-markdown img')).toHaveCount(0);
  expect(await page.evaluate(() => '__knowledge_xss' in window)).toBe(false);
  await expect(page.getByRole('link', {name: 'Hostile link'})).toHaveCount(0);
  await noStoredCredentials(page);
});
import { chromium } from '@playwright/test';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

test('actual 200 percent browser zoom preserves named navigation, dialog focus and keyboard return', async ({}, info) => {
  const monitoring = await prepareMonitoring();
  const profile = await mkdtemp(path.join(tmpdir(), 'acp-phase16-zoom-'));
  const context = await chromium.launchPersistentContext(profile, {
    headless: true, viewport: null, ignoreHTTPSErrors: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? {executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE} : {}),
    args: ['--window-size=1440,1100'], reducedMotion: 'reduce',
  });
  try {
    const settings = context.pages()[0]!;
    await settings.goto('chrome://settings/appearance');
    await settings.getByRole('combobox', {name: 'Page zoom', exact: true}).selectOption('2');
    const page = await context.newPage();
    await localLogin(page, 'owner');
    await goto(page, 'people');
    await expect(page.getByRole('table', {name: 'Organization people'})).toBeVisible();
    expect(await page.evaluate(() => devicePixelRatio)).toBe(2);
    expect(await page.evaluate(() => innerWidth)).toBeLessThanOrEqual(720);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.getByRole('link', {name: 'Agent Context Organization console · My workspace'})).toBeVisible();
    const invite = page.getByRole('button', {name: 'Invite a person'});
    await invite.focus(); await page.keyboard.press('Enter');
    await expect(page.getByRole('dialog', {name: 'Invite a person'})).toBeVisible();
    await page.keyboard.press('Tab');
    expect(await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))).toBe(true);
    await page.keyboard.press('Escape'); await expect(invite).toBeFocused();
    // Playwright's CSS viewport capture is cropped at actual browser zoom.
    // Raw compositor capture preserves the entire physical viewport.
    const cdp = await context.newCDPSession(page);
    for (const theme of ['light', 'dark']) {
      if (theme === 'dark') await page.getByRole('button', {name: 'Use dark theme'}).click();
      const capture = await cdp.send('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
      const png = Buffer.from(capture.data as string, 'base64');
      expect(png.readUInt32BE(16)).toBe(await page.evaluate(() => Math.round(innerWidth * devicePixelRatio)));
      await writeFile(info.outputPath('actual-browser-200-percent-' + theme + '.png'), png);
    }
    await goto(page, 'audit?' + monitoringQuery(monitoring, {source: 'governance', scope: 'org'}));
    await expect(page.getByRole('table', {name: 'Governance events'})).toBeVisible();
    const timeline = page.getByRole('region', {name: 'Governance events', exact: true});
    await timeline.scrollIntoViewIfNeeded(); await timeline.focus();
    await timeline.press('ArrowRight');
    await expect.poll(() => timeline.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
    expect(await page.evaluate(() => devicePixelRatio)).toBe(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    for (const theme of ['light', 'dark']) {
      if (await page.evaluate(() => document.documentElement.dataset.theme) !== theme)
        await page.getByRole('button', {name: theme === 'dark' ? 'Use dark theme' : 'Use light theme'}).click();
      for (const edge of ['start', 'end']) {
        await timeline.evaluate((element, selected) => {element.scrollLeft = selected === 'start' ? 0 : element.scrollWidth;}, edge);
        await timeline.scrollIntoViewIfNeeded();
        const capture = await cdp.send('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
        await writeFile(info.outputPath(`actual-audit-200-percent-${theme}-${edge}.png`), Buffer.from(capture.data as string, 'base64'));
      }
    }
    await cdp.detach();
    await noStoredCredentials(page);
  } finally {await context.close(); await rm(profile, {recursive: true, force: true});}
});
