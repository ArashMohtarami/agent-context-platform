import AxeBuilder from '@axe-core/playwright';
import { control, environment, expect, localLogin, noStoredCredentials, test } from './fixture';
import { api, goto } from './management-fixture';

test.setTimeout(240000);
const base = '/organizations/org-a/knowledge';
test.beforeEach(async () => {await control('knowledge');});

test('repeated-ID search and direct APIs enforce action resources and private memory', async ({page}) => {
  await localLogin(page);
  await goto(page, 'knowledge/search?project=payment-service');
  await page.getByLabel('Search terms', {exact: true}).fill('isolation');
  await page.getByLabel('Search source').selectOption('project_context');
  const answer = page.waitForResponse(r => r.url().endsWith('/knowledge/search') && r.request().method() === 'POST');
  await page.getByRole('button', {name: 'Search knowledge', exact: true}).click();
  const response = await answer; expect(response.ok()).toBe(true);
  const body = await response.json(); expect(body.results.length).toBeGreaterThan(0);
  expect(JSON.stringify(body)).toContain('PRIVATE_SENTINEL_A');
  expect(JSON.stringify(body)).not.toMatch(/PRIVATE_SENTINEL_B|PRIVATE_AGENT_SENTINEL/);
  await expect(page.locator('.knowledge-markdown strong').first()).toHaveText('Safe bold');
  expect(await page.evaluate(() => '__knowledge_xss' in window)).toBe(false);
  await expect(page.locator('.knowledge-markdown script, .knowledge-markdown img, .knowledge-markdown iframe')).toHaveCount(0);
  await expect(page.getByRole('link', {name: 'Hostile link'})).toHaveCount(0);
  const contextId = body.results.find((r: {metadata: {context_id?: string}}) => r.metadata.context_id).metadata.context_id;
  expect((await api(page, base + '/context/' + contextId + '?project_id=payment-service', {expected_revision: 1, content: 'Must be refused'}, 'PATCH')).status()).toBe(403);
  expect((await api(page, base + '/context?project_id=payments-api')).status()).toBe(403);
  expect((await api(page, base + '/memories?scope=agent')).status()).toBe(403);
  expect((await api(page, base.replace('org-a', 'org-b') + '/projects')).status()).toBe(403);
  const deniedSearch = await api(page, base + '/search', {query: 'isolation', project_id: 'payments-api', sources: ['project_context']});
  expect(deniedSearch.ok()).toBe(true); expect((await deniedSearch.json()).results).toEqual([]);
  await noStoredCredentials(page);
});

test('project creation, revision conflict recovery, intentional memory and skill history use real services', async ({page}, info) => {
  await localLogin(page, 'owner');
  await goto(page, 'knowledge/projects');
  await page.getByRole('button', {name: 'Create project', exact: true}).click();
  await page.getByLabel('Project ID', {exact: true}).fill('fictional-workspace-project');
  await page.getByLabel('Project name', {exact: true}).fill('Fictional workspace project');
  await page.getByLabel('Project description').fill('Created deliberately through a verified human session.');
  await page.getByLabel('Repository reference').fill('fictional/workspace');
  await page.getByRole('dialog').getByRole('button', {name: 'Create project', exact: true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('link', {name: 'Fictional workspace project', exact: true})).toBeVisible();
  await goto(page, 'knowledge/projects/payment-service');
  await page.getByRole('button', {name: 'Edit Context A', exact: true}).first().click();
  await page.getByLabel('Context Markdown').fill('My preserved human draft');
  await control('knowledge-conflict');
  await page.getByRole('button', {name: 'Save context', exact: true}).click();
  await expect(page.getByText(/Revision conflict. Your unsaved text is preserved/)).toBeVisible();
  await expect(page.getByLabel('Context Markdown')).toHaveValue('My preserved human draft');
  await page.getByRole('button', {name: 'Load current revision'}).click();
  await expect(page.getByText('Concurrent saved revision', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Use this revision with my draft'}).click();
  await page.getByRole('button', {name: 'Save context', exact: true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByText('My preserved human draft', {exact: true})).toBeVisible();
  await goto(page, 'knowledge/memory');
  await page.getByRole('button', {name: 'Create memory', exact: true}).click();
  await page.getByLabel('Memory summary').fill('Fictional workspace decision');
  await page.getByLabel('Memory Markdown').fill('Intentional human knowledge');
  await page.getByRole('button', {name: 'Save memory', exact: true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  let card = page.locator('.knowledge-card').filter({has: page.getByRole('heading', {name: 'Fictional workspace decision', exact: true})});
  await card.getByRole('button', {name: 'Supersede memory'}).click();
  await page.getByLabel('Memory summary').fill('Fictional corrected decision');
  await page.getByLabel('Memory Markdown').fill('Corrected human knowledge');
  await page.getByRole('button', {name: 'Record replacement'}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  card = page.locator('.knowledge-card').filter({has: page.getByRole('heading', {name: 'Fictional corrected decision', exact: true})});
  await card.getByRole('button', {name: 'Previous memory'}).click();
  await expect(page.getByLabel('Memory Markdown')).toHaveValue('Intentional human knowledge');
  await page.getByRole('button', {name: 'Close dialog'}).click();
  await card.getByRole('button', {name: 'Inspect memory'}).click();
  await page.getByLabel('Archive memory', {exact: true}).check();
  await page.getByRole('button', {name: 'Save memory', exact: true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByLabel('memories status').selectOption('archived');
  await expect(page.getByRole('heading', {name: 'Fictional corrected decision', exact: true})).toBeVisible();
  await goto(page, 'knowledge/skills/retry-skill');
  await expect(page.getByRole('heading', {name: 'Version history', exact: true})).toBeVisible();
  await expect(page.getByRole('table', {name: 'Canonical skill versions'})).toContainText('1.0.0');
  await page.getByLabel('Skill version', {exact: true}).selectOption('0.9.0');
  await expect(page.getByText('HISTORICAL_SKILL_A', {exact: true})).toBeVisible();
  await expect(page.getByText('HISTORICAL_SKILL_B', {exact: true})).toHaveCount(0);
  await page.screenshot({path: info.outputPath('knowledge-skill-history.png'), fullPage: true});
  await noStoredCredentials(page);
});

test('organization switch clears query, old results and delayed responses before entering same-ID org B', async ({page}) => {
  await control('prefer-b'); await localLogin(page);
  await goto(page, 'knowledge/search?project=payment-service');
  await page.getByLabel('Search terms', {exact: true}).fill('isolation');
  await page.getByLabel('Search source').selectOption('project_context');
  let release = () => {}; let started = () => {};
  const ready = new Promise<void>(resolve => {started = resolve;});
  await page.route('**/knowledge/search', async route => {
    const response = await route.fetch(); started();
    await new Promise<void>(resolve => {release = resolve;});
    try {await route.fulfill({response});} catch { /* The old request was cancelled by the verified context switch. */ }
  });
  await page.getByRole('button', {name: 'Search knowledge', exact: true}).click(); await ready;
  await page.getByLabel('Active organization').selectOption('org-b');
  await page.getByRole('button', {name: 'Discard and switch'}).click();
  await expect(page.getByLabel('Active organization')).toHaveValue('org-b');
  release(); await page.unroute('**/knowledge/search');
  await page.goto((await environment()).origin + '/console/o/org-b/knowledge/search?project=payment-service');
  await expect(page.getByLabel('Search terms', {exact: true})).toHaveValue('');
  await expect(page.getByText('PRIVATE_SENTINEL_A', {exact: false})).toHaveCount(0);
  await page.getByLabel('Search terms', {exact: true}).fill('isolation');
  await page.getByLabel('Search source').selectOption('project_context');
  await page.getByRole('button', {name: 'Search knowledge', exact: true}).click();
  await expect(page.getByText(/PRIVATE_SENTINEL_B/).first()).toBeVisible();
  await expect(page.getByText(/PRIVATE_SENTINEL_A/)).toHaveCount(0);
  await page.goBack(); await expect(page.getByText(/PRIVATE_SENTINEL_A/)).toHaveCount(0);
  await noStoredCredentials(page);
});

test('knowledge responsive states, both themes, keyboard and safe rendered Markdown are reviewed', async ({page}, info) => {
  await localLogin(page, 'owner');
  for (const section of ['knowledge/projects/payment-service', 'knowledge/memory', 'knowledge/skills/retry-skill', 'knowledge/search']) {
    await goto(page, section); await expect(page.locator('.skeleton')).toHaveCount(0);
    for (const width of [360, 768, 1440]) for (const theme of ['light', 'dark']) {
      await page.setViewportSize({width, height: 1000}); await page.evaluate(t => {document.documentElement.dataset.theme = t;}, theme);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({path: info.outputPath(section.replaceAll('/', '-') + '-' + theme + '-' + width + '.png'), fullPage: true});
    }
    const violations = (await new AxeBuilder({page}).analyze()).violations;
    expect(violations).toEqual([]);
  }
  await page.setViewportSize({width: 720, height: 1000}); await page.evaluate(() => {document.documentElement.style.fontSize = '200%';});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.keyboard.press('Tab'); expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe('BODY');
});
