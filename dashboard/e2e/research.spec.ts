import AxeBuilder from '@axe-core/playwright';
import { control, environment, expect, localLogin, noStoredCredentials, test } from './fixture';
import { api, goto } from './management-fixture';

test.setTimeout(240000);
const project = 'payment-retry-research'; const base = '/organizations/org-a/research'; const p = base + '/projects/' + project;
let foreignSnapshot = '';
test.beforeEach(async () => {foreignSnapshot = (await control<{foreign_snapshot_id: string}>('research')).foreign_snapshot_id;});

test('ingest, inspect, independently review and promote current evidence through real browser sessions', async ({page, browser}, info) => {
  await localLogin(page); await goto(page, 'research/' + project);
  await page.getByRole('button', {name: 'Register source', exact: true}).click();
  await page.getByLabel('Source ID', {exact: true}).fill('browser-evidence');
  await page.getByLabel('Source title', {exact: true}).fill('Fictional browser evidence');
  await page.getByLabel('Source URI', {exact: true}).fill('manual:browser-evidence');
  await page.getByRole('button', {name: 'Save register source'}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button', {name: 'Ingest Fictional browser evidence'}).click();
  await page.getByLabel('Evidence text').fill('PRIVATE_RESEARCH_A. Bounded evidence supports safe retry handling.\n\n**Safe research bold** <script>window.research_xss=1</script> [hostile](javascript:bad)');
  const ingestionResponse = page.waitForResponse(r => r.url().endsWith('/ingest') && r.request().method() === 'POST');
  await page.getByRole('button', {name: 'Run ingestion'}).click();
  const ingestion = await ingestionResponse; expect(ingestion.ok()).toBe(true); const ingested = await ingestion.json(); expect(ingested.job.status).toBe('completed');
  await page.getByRole('button', {name: 'Close and refresh'}).click();
  await expect(page.getByText('Safe research bold', {exact: true})).toBeVisible();
  expect(await page.evaluate(() => 'research_xss' in window)).toBe(false);
  await expect(page.locator('.knowledge-markdown script,.knowledge-markdown iframe,.knowledge-markdown img')).toHaveCount(0);
  await expect(page.getByRole('link', {name: 'hostile', exact: true})).toHaveCount(0);
  const docs = await (await api(page, p + '/records/documents?source_id=browser-evidence')).json(); const doc = docs.items[0];
  const chunks = await (await api(page, p + '/sources/browser-evidence/snapshots/' + doc.snapshot_id + '/documents/' + doc.document_id + '/chunks')).json(); const chunk = chunks.items[0];
  await page.getByRole('button', {name: 'Notes & evidence'}).click();
  await page.getByRole('button', {name: 'Create claim', exact: true}).click();
  await page.getByLabel('Claim ID', {exact: true}).fill('browser-claim'); await page.getByLabel('Claim statement').fill('Retry handling must be evidence-backed.');
  await page.getByRole('button', {name: 'Save create claim'}).click(); await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button', {name: 'Attach citation', exact: true}).click();
  for (const [label, value] of [['Citation ID', 'browser-citation'], ['Claim ID', 'browser-claim'], ['Source ID', 'browser-evidence'], ['Snapshot ID', doc.snapshot_id], ['Chunk ID', chunk.chunk_id], ['Exact locator JSON from inspected chunk', JSON.stringify(chunk.locator)], ['Exact evidence excerpt', 'Bounded evidence supports safe retry handling.']]) await page.getByLabel(label!, {exact: true}).fill(value!);
  await page.getByRole('button', {name: 'Save attach citation'}).click(); await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.locator('.knowledge-card').filter({has: page.getByRole('heading', {name: 'Retry handling must be evidence-backed.', exact: true})}).getByRole('button', {name: 'Inspect evidence', exact: true}).click();
  await expect(page.getByText(/up to 50 citations/)).toBeVisible(); await expect(page.getByText(doc.snapshot_id, {exact: true})).toBeVisible();
  await page.screenshot({path: info.outputPath('research-evidence.png'), fullPage: false}); await page.getByRole('button', {name: 'Close dialog'}).click();
  await page.getByRole('button', {name: 'Review & promotion'}).click(); await page.getByRole('button', {name: 'Propose knowledge', exact: true}).click();
  for (const [label, value] of [['Candidate ID', 'browser-candidate'], ['Candidate title', 'Fictional browser finding'], ['Candidate Markdown', 'Reviewed bounded retries'], ['Target project ID', 'payment-service'], ['Claim IDs (comma separated)', 'browser-claim'], ['Citation IDs (comma separated)', 'browser-citation']]) await page.getByLabel(label!, {exact: true}).fill(value!);
  await page.getByRole('button', {name: 'Save propose knowledge'}).click(); await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button', {name: 'Review Fictional browser finding'}).click(); await expect(page.getByRole('button', {name: 'Confirm decision'})).toBeDisabled(); await page.getByRole('button', {name: 'Close dialog'}).click();
  expect((await api(page, p + '/candidates/browser-candidate/decision', {expected_revision: 1, decision: 'approved'})).status()).toBe(403);
  expect((await api(page, base.replace('org-a', 'org-b') + '/projects/' + project)).status()).toBe(403);
  const reviewerContext = await browser.newContext({ignoreHTTPSErrors: true}); const reviewer = await reviewerContext.newPage();
  await localLogin(reviewer, 'reviewer'); await goto(reviewer, 'research/' + project + '?tab=review');
  expect((await api(reviewer, p + '/candidates/browser-candidate/decision', {expected_revision: 999, decision: 'approved'})).status()).toBe(409);
  await reviewer.getByRole('button', {name: 'Review Fictional browser finding'}).click(); await reviewer.getByLabel('Decision rationale').fill('Independently inspected source, snapshot and exact excerpt.');
  const decisionResponse = reviewer.waitForResponse(r => r.url().endsWith('/browser-candidate/decision') && r.request().method() === 'POST');
  await reviewer.getByRole('button', {name: 'Confirm decision'}).click(); const decision = await decisionResponse; expect(decision.ok()).toBe(true); const approved = await decision.json(); expect(approved.candidate.revision).toBe(2); expect(approved.event.reviewer).toBe('reviewer');
  await expect(reviewer.getByRole('dialog')).toHaveCount(0);
  expect((await api(reviewer, base + '/candidates/browser-candidate/promote', {research_project_id: project, expected_revision: 2})).status()).toBe(403);
  await reviewer.screenshot({path: info.outputPath('research-reviewed.png'), fullPage: true});
  const ownerContext = await browser.newContext({ignoreHTTPSErrors: true}); const owner = await ownerContext.newPage();
  await localLogin(owner, 'owner'); await goto(owner, 'research/' + project + '?tab=review'); await owner.getByRole('button', {name: 'Promote Fictional browser finding'}).click();
  const promotionResponse = owner.waitForResponse(r => r.url().endsWith('/browser-candidate/promote')); await owner.getByRole('button', {name: 'Confirm promotion'}).click(); const promoted = await promotionResponse; expect(promoted.ok()).toBe(true); const target = await promoted.json(); expect(target.target.provenance.source_snapshot_ids).toEqual([doc.snapshot_id]);
  await expect(owner.getByRole('dialog')).toHaveCount(0); await owner.getByRole('link', {name: 'Target project payment-service'}).click(); await expect(owner.getByRole('heading', {name: 'Fictional browser finding', exact: true})).toBeVisible();
  await noStoredCredentials(page); await noStoredCredentials(reviewer); await noStoredCredentials(owner); await reviewerContext.close(); await ownerContext.close();
});

test('bounded upload, foreign citation and revoked job download fail safely', async ({page}) => {
  await localLogin(page); await goto(page, 'research/' + project);
  await expect(page.getByRole('button', {name: 'Register source', exact: true})).toBeVisible();
  await expect(page.locator('.skeleton')).toHaveCount(0);
  await page.waitForLoadState('networkidle');
  expect((await api(page, base + '/sources', {research_project_id: project, source_id: 'browser-evidence', source_type: 'file', uri: 'file:///uploads/fictional.txt', title: 'Fictional file'})).status()).toBe(201);
  await page.reload(); await page.getByRole('button', {name: 'Ingest Fictional file'}).click(); await page.getByLabel('Ingestion method').selectOption('file');
  await page.getByLabel('Evidence file').setInputFiles({name: 'fictional.txt', mimeType: 'text/plain', buffer: Buffer.from('Fictional local file evidence')});
  const result = page.waitForResponse(r => r.url().endsWith('/ingest')); await page.getByRole('button', {name: 'Run ingestion'}).click(); const job = (await (await result).json()).job; expect(job.status).toBe('completed');
  await page.getByRole('button', {name: 'Close and refresh'}).click();
  expect((await api(page, p + '/sources/browser-evidence/ingest', {content: 'x'.repeat(8193), content_type: 'text/plain'})).status()).toBe(422);
  expect((await api(page, base + '/claims', {research_project_id: project, claim_id: 'foreign-claim', statement: 'A mixed-org citation must fail.'})).status()).toBe(201);
  expect((await api(page, base + '/citations', {research_project_id: project, citation_id: 'foreign-citation', claim_id: 'foreign-claim', source_id: 'browser-evidence', snapshot_id: foreignSnapshot, locator: {}, evidence_excerpt: 'PRIVATE_RESEARCH_B', relation: 'supports'})).status()).toBe(422);
  const downloadPath = p + '/sources/browser-evidence/jobs/' + job.job_id + '/documents'; expect((await api(page, downloadPath)).status()).toBe(200);
  await control('research-revoke'); expect((await page.request.get((await environment()).origin + '/api/v6' + downloadPath)).status()).toBe(401);
});

test('research layout, light/dark states, keyboard and bounded empty views are accessible', async ({page}, info) => {
  await localLogin(page, 'owner');
  for (const tab of ['sources', 'evidence', 'review', 'briefs', 'freshness']) {
    await goto(page, 'research/' + project + '?tab=' + tab); await expect(page.locator('.skeleton')).toHaveCount(0);
    for (const width of [360, 768, 1440]) for (const theme of ['light', 'dark']) {await page.setViewportSize({width, height: 1000}); await page.evaluate(t => {document.documentElement.dataset.theme = t;}, theme); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); await page.screenshot({path: info.outputPath('research-' + tab + '-' + theme + '-' + width + '.png'), fullPage: true});}
    expect((await new AxeBuilder({page}).analyze()).violations).toEqual([]);
  }
  await page.setViewportSize({width: 720, height: 1000}); await page.evaluate(() => {document.documentElement.style.fontSize = '200%';}); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); await page.keyboard.press('Tab'); expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe('BODY');
  expect((await environment()).origin).toMatch(/^https:/);
});

test('notes, structured briefs and skill proposals use current records without changing canonical Git', async ({page, browser}, info) => {
  await localLogin(page); await goto(page, 'research/' + project + '?tab=evidence');
  await page.getByRole('button', {name: 'Create note', exact: true}).click();
  await page.getByLabel('Note ID').fill('browser-note'); await page.getByLabel('Note title').fill('Fictional working note'); await page.getByLabel('Note Markdown').fill('**Working note** with unresolved evidence.');
  await page.getByRole('button', {name: 'Save create note'}).click(); await expect(page.getByRole('dialog')).toHaveCount(0);
  const noteCard = page.locator('.knowledge-card').filter({has: page.getByRole('heading', {name: 'Fictional working note'})});
  await noteCard.getByRole('button', {name: 'Edit note'}).click(); await page.getByLabel('Note Markdown').fill('**Revised working note** with unresolved evidence.'); await page.getByRole('button', {name: 'Save edit note'}).click(); await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(noteCard).toContainText('revision 2');
  await page.getByRole('button', {name: 'Create claim', exact: true}).click(); await page.getByLabel('Claim ID', {exact: true}).fill('proposal-claim'); await page.getByLabel('Claim statement').fill('A proposal requires human Git review.'); await page.getByRole('button', {name: 'Save create claim'}).click(); await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button', {name: 'Briefs & decisions'}).click(); await page.getByRole('button', {name: 'Build brief', exact: true}).click(); await page.getByLabel('Brief ID').fill('browser-brief'); await page.getByLabel('Brief scope').fill('Bounded fictional claims with explicit uncertainty.'); await page.getByRole('button', {name: 'Save build brief'}).click(); await expect(page.getByRole('dialog')).toHaveCount(0); await expect(page.getByText('browser-brief · draft · revision 1', {exact: false})).toBeVisible();
  await page.getByRole('button', {name: 'Review & promotion'}).click(); await page.getByRole('button', {name: 'Propose skill patch', exact: true}).click();
  for (const [label, value] of [['Proposal ID', 'browser-proposal'], ['Skill ID', 'retry-skill'], ['Current skill version', '1.0.0'], ['Proposed change', 'Document bounded retries'], ['Proposed patch (inert text)', '<script>window.patch_xss=1</script>\n+Document bounded retries'], ['Patch rationale', 'Keep Git review explicit'], ['Claim IDs (comma separated)', 'proposal-claim']]) await page.getByLabel(label!, {exact: true}).fill(value!);
  await page.getByRole('button', {name: 'Save propose skill patch'}).click(); await expect(page.getByRole('dialog')).toHaveCount(0);
  const ctx = await browser.newContext({ignoreHTTPSErrors: true}); const reviewer = await ctx.newPage(); await localLogin(reviewer, 'reviewer'); await goto(reviewer, 'research/' + project + '?tab=review');
  await reviewer.locator('.knowledge-card').filter({has: reviewer.getByRole('heading', {name: 'Document bounded retries', exact: true})}).getByRole('button', {name: 'Review skill proposal'}).click(); await reviewer.getByRole('button', {name: 'Confirm decision'}).click(); await expect(reviewer.getByRole('dialog')).toHaveCount(0);
  await expect(reviewer.getByText('browser-proposal · approved · revision 2 · generated proposal', {exact: true})).toBeVisible(); expect(await reviewer.evaluate(() => 'patch_xss' in window)).toBe(false);
  await reviewer.screenshot({path: info.outputPath('research-skill-proposal.png'), fullPage: true}); await reviewer.locator('.knowledge-card').filter({has: reviewer.getByRole('heading', {name: 'Document bounded retries', exact: true})}).getByRole('link', {name: 'retry-skill · base version 1.0.0'}).click(); await expect(reviewer.getByText('Document bounded retries', {exact: true})).toHaveCount(0); await expect(reviewer.getByRole('table', {name: 'Canonical skill versions'})).toContainText('1.0.0'); await noStoredCredentials(reviewer); await ctx.close();
});
