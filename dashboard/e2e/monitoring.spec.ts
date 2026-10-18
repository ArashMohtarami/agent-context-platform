import AxeBuilder from '@axe-core/playwright';
import { control, environment, expect, localLogin, noStoredCredentials, test } from './fixture';
import { api, goto } from './management-fixture';
import { prepare, query } from './monitoring-fixture';
import type { MonitoringFixture } from './monitoring-fixture';
let f:MonitoringFixture;test.setTimeout(240000);test.beforeEach(async()=>{f=await prepare();});

test('personal, team and leadership summaries match permitted raw fixture events in multiple timezones',async({page,browser},info)=>{
 for(const [user,scope,key,path] of [['member','own','own','workspace'],['lead','team','alpha','team-overview'],['other','org','org','leadership']] as const){
  const context=await browser.newContext({ignoreHTTPSErrors:true});const p=await context.newPage();await localLogin(p,user);const expected=f.expected[key]!;
  for(const timezone of ['UTC','Asia/Tehran','America/New_York']){
   const params=query(f,{scope,timezone,...(scope==='team'?{team_id:'alpha'}:{})});const response=p.waitForResponse(r=>r.url().includes('/overview?'));await goto(p,path+'?'+params);const data=await(await response).json();expect(data.observed_count).toBe(expected.count);expect(data.outcomes).toEqual(expected.outcomes);expect(data.measures.latency.sample_count).toBe(expected.samples);expect(data.measures.latency.value).toBeCloseTo(expected.latency??0);expect(data.query.timezone).toBe(timezone);expect(data.buckets.map((b:{start:string;end:string;count:number})=>({start_ms:Date.parse(b.start),end_ms:Date.parse(b.end),count:b.count}))).toEqual(expected.buckets[timezone]);expect(data.buckets.reduce((n:number,b:{count:number})=>n+b.count,0)).toBe(expected.count);
   await expect(p.getByTestId('observed-count')).toContainText(String(expected.count)+' recorded gateway reports');await expect(p.getByRole('table',{name:'Recorded intervals (chart alternative)'})).toBeVisible();await expect(p.getByText('PRIVATE_MONITOR_BODY')).toHaveCount(0);
  }
  const detail=await(await api(p,'/organizations/org-a/activity?'+query(f,{scope,...(scope==='team'?{team_id:'alpha'}:{})}))).json();expect(detail.items.map((r:{event_id:string})=>r.event_id).sort()).toEqual(expected.ids);expect(detail.items.some((r:{event_id:string})=>r.event_id===f.foreign_event_id)).toBe(false);
  if(user==='other'){expect((await api(p,'/organizations/org-a/knowledge/projects/payment-service')).status()).toBe(403);expect((await api(p,'/organizations/org-a/activity?'+query(f,{source:'governance',scope:'org'}))).status()).toBe(403);}
  if(user==='lead')expect((await api(p,'/organizations/org-a/overview?'+query(f,{scope:'org'}))).status()).toBe(403);
  expect((await api(p,'/organizations/org-b/overview?'+query(f,{scope:'org'}))).status()).toBe(403);await p.evaluate(()=>window.scrollTo(0,0));await p.screenshot({path:info.outputPath('monitoring-'+user+'.png'),fullPage:true});await noStoredCredentials(p);await context.close();
 }
 await localLogin(page,'owner');await goto(page,'leadership?'+query(f,{scope:'org'}));await page.getByLabel('Project filter ID (optional)').fill('foreign-only-project');const zero=page.waitForResponse(r=>r.url().includes('/overview?')&&r.url().includes('foreign-only-project'));await page.getByRole('button',{name:'Apply monitoring filters'}).click();expect((await(await zero).json()).observed_count).toBe(0);await expect(page.getByText('No recorded buckets')).toBeVisible();
});

test('collector states, responsive charts, text alternatives, both themes and bounded errors remain honest',async({page},info)=>{
 await localLogin(page,'owner');await goto(page,'leadership?'+query(f,{scope:'org'}));await expect(page.getByTestId('observed-count')).toBeVisible();
 for(const width of [360,768,1440])for(const theme of ['light','dark']){await page.setViewportSize({width,height:1000});await page.evaluate(t=>{document.documentElement.dataset.theme=t;},theme);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:info.outputPath('monitoring-'+theme+'-'+width+'.png'),fullPage:true});}
 expect((await new AxeBuilder({page}).analyze()).violations).toEqual([]);await control('monitoring-state',{state:'gaps'});await page.getByRole('button',{name:'Refresh recorded observations'}).click();await expect(page.getByRole('table',{name:'Declared missing intervals'})).toBeVisible();
 await control('monitoring-state',{state:'stale'});await page.getByRole('button',{name:'Refresh recorded observations'}).click();await expect(page.getByText(/Stale collection declaration/)).toBeVisible();await control('monitoring-state',{state:'missing'});await page.getByRole('button',{name:'Refresh recorded observations'}).click();await expect(page.getByText(/Collector not configured/)).toBeVisible();
 await page.getByLabel('Bucket timezone (IANA)').fill('Fake/Zone');await page.getByRole('button',{name:'Apply monitoring filters'}).click();await expect(page.getByText(/Use explicit ISO instants/)).toBeVisible();await page.setViewportSize({width:720,height:1000});await page.evaluate(()=>{document.documentElement.style.fontSize='200%';});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.keyboard.press('Tab');expect(await page.evaluate(()=>document.activeElement?.tagName)).not.toBe('BODY');
});

test('moving a worker cannot reveal old-team events to the new lead',async({page})=>{
 await control('monitoring-state',{state:'move'});await localLogin(page,'reviewer');const params=query(f,{scope:'team',team_id:'beta'});const response=page.waitForResponse(r=>r.url().includes('/overview?'));await goto(page,'team-overview?'+params);expect((await(await response).json()).observed_count).toBe(f.expected.beta!.count);await page.getByRole('link',{name:'Open content-free activity timeline'}).click();const table=page.getByRole('table',{name:'Content-free activity events'});await expect(table).toContainText('Event-time team: beta');await expect(table).not.toContainText('Event-time team: alpha');expect((await api(page,'/organizations/org-a/activity?'+query(f,{scope:'team',team_id:'alpha'}))).status()).toBe(403);
});


test('a real organization switch loads tenant B matching IDs without retaining tenant A totals',async({page})=>{
 await control('monitoring-state',{state:'org-b'});await localLogin(page,'member');await goto(page,'workspace?'+query(f,{scope:'own'}));await expect(page.getByTestId('observed-count')).toContainText(String(f.expected.own!.count)+' recorded gateway reports');
 await page.getByLabel('Active organization').selectOption('org-b');await expect(page.getByRole('heading',{name:'Welcome, member'})).toBeVisible();await expect(page.getByLabel('Active organization')).toHaveValue('org-b');
 const response=page.waitForResponse(r=>r.url().includes('/organizations/org-b/overview?'));await page.goto((await environment()).origin+'/console/o/org-b/leadership?'+query(f,{scope:'org'}));const result=await(await response).json();expect(result.org_id).toBe('org-b');expect((await api(page,'/organizations/org-b/knowledge/projects/payment-service')).status()).toBe(403);expect(result.observed_count).toBe(f.org_b_count);await expect(page.getByTestId('observed-count')).toContainText(String(f.org_b_count)+' recorded gateway reports');expect((await api(page,'/organizations/org-a/overview?'+query(f,{scope:'org'}))).status()).toBe(403);await noStoredCredentials(page);
});
