import { writeFile } from 'node:fs/promises';
import { control, environment, expect, localLogin, open, test } from './fixture';
import { goto } from './management-fixture';

interface Measures {lcp_ms: number | null; cls: number; interaction_ms: number[]}

test('records cold browser timings at the declared CPU and network profile', async ({page}, info) => {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.setCacheDisabled', {cacheDisabled: true});
  await cdp.send('Network.emulateNetworkConditions', {offline: false, latency: 100,
    downloadThroughput: 1250000, uploadThroughput: 1250000, connectionType: 'cellular4g'});
  await cdp.send('Emulation.setCPUThrottlingRate', {rate: 4});
  await page.addInitScript(() => {
    const measures: Measures = {lcp_ms: null, cls: 0, interaction_ms: []};
    Object.defineProperty(window, '__acceptanceMetrics', {value: measures});
    new PerformanceObserver(list => {for (const entry of list.getEntries()) measures.lcp_ms = entry.startTime;}).observe({type: 'largest-contentful-paint', buffered: true});
    new PerformanceObserver(list => {for (const entry of list.getEntries()) {
      const shift = entry as PerformanceEntry & {hadRecentInput: boolean; value: number};
      if (!shift.hadRecentInput) measures.cls += shift.value;
    }}).observe({type: 'layout-shift', buffered: true});
    new PerformanceObserver(list => {for (const entry of list.getEntries()) {
      if ((entry as PerformanceEntry & {interactionId: number}).interactionId) measures.interaction_ms.push(entry.duration);
    }}).observe({type: 'event', buffered: true, durationThreshold: 16} as PerformanceObserverInit);
  });
  const metrics = () => page.evaluate(() => (window as unknown as {__acceptanceMetrics: Measures}).__acceptanceMetrics);
  const painted = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await open(page);
  await expect(page.getByRole('heading', {name: 'Your context, connected.'})).toBeVisible();
  await painted();
  const coldLogin = await metrics();
  const corpus = await control<{source_count: number; inserted: number; org_id: string; assignment_count: number}>('performance-directory');
  expect(corpus.inserted).toBe(10000); expect(corpus.assignment_count).toBe(0);
  await localLogin(page, 'owner');
  await goto(page, 'people');
  await expect(page.getByRole('table', {name: 'Organization people'})).toBeVisible();
  await expect(page.locator('.skeleton')).toHaveCount(0);
  const rows = await page.getByRole('table', {name: 'Organization people'}).locator('tbody tr').count();
  await page.getByRole('button', {name: 'Use dark theme'}).click();
  await painted();
  const directory = await metrics();
  const {origin} = await environment();
  const me = await (await page.request.get(origin + '/api/v6/me/console')).json();
  const apiSamples: {ms: number; status: number}[] = [];
  await Promise.all(Array.from({length: 20}, async () => {
    const start = performance.now();
    const response = await page.request.get(origin + '/api/v6/organizations/org-a/members?limit=20',
      {headers: {'X-Session-Context': me.me.policy_context.context_id}});
    const body = await response.json();
    apiSamples.push({ms: performance.now() - start, status: response.status()});
    expect(response.status()).toBe(200); expect(body.items).toHaveLength(20);
    expect(body.next_cursor).toBeTruthy();
    expect(body.items.every((m: {org_id: string}) => m.org_id === 'org-a')).toBe(true);
  }));
  apiSamples.sort((a,b) => a.ms - b.ms);
  const queryPlan = await control<{returned_with_cursor_probe: number; documents_examined: number;
    keys_examined: number; documents_per_returned: number; scope: string}>('performance-directory-explain');
  expect(queryPlan.returned_with_cursor_probe).toBe(21);
  await writeFile(info.outputPath('browser-performance.json'), JSON.stringify({
    browser: await page.context().browser()?.version(), origin: new URL((await environment()).origin).hostname,
    cpu_slowdown: 4, network_mbps: 10, rtt_ms: 100, cache: 'disabled', viewport: page.viewportSize(),
    cold_login: coldLogin, directory, rendered_rows: rows,
    corpus: {...corpus, description: 'real fictional persisted identities/memberships; no added assignments'},
    directory_api: {concurrency: 20, page_size: 20, samples: apiSamples, p95_ms: apiSamples[18]!.ms,
      target_p95_ms: 500, query_plan: queryPlan},
    targets: {lcp_ms: 2500, cls: 0.1, interaction_ms: 200},
    interaction_definition: 'lab Event Timing samples; not a field INP claim',
  }, null, 2));
  expect(rows).toBe(25); // Shipped UI page size; the separate REST workload requests 20.
  expect(corpus.source_count).toBeGreaterThanOrEqual(10000);
  expect(directory.lcp_ms).not.toBeNull();
  await cdp.detach();
});
