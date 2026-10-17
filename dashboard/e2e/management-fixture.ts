import type { Page } from '@playwright/test';
import { environment, expect } from './fixture';
import type { Authoring, Grant } from '../src/management/contracts';
export async function api(page: Page, path: string, body?: unknown, method = 'POST') {
  const {origin} = await environment();
  const me = await (await page.request.get(origin + '/api/v6/me/console')).json();
  const csrf = await (await page.request.get(origin + '/api/v6/auth/csrf')).json();
  return page.request.fetch(origin + '/api/v6' + path, {method: body === undefined ? 'GET' : method, headers: {Origin: origin, 'X-CSRF-Token': csrf.csrf_token, 'X-Session-Context': me.me.policy_context.context_id}, ...(body === undefined ? {} : {data: body})});
}
export async function authoring(page: Page): Promise<Authoring> {
  const result = await api(page, '/organizations/org-a/management/context'); expect(result.ok()).toBe(true); return result.json();
}
export async function tuple(page: Page, label: string, g: Grant) {
  await page.getByRole('button', {name: 'Add ' + label.toLowerCase() + ' row', exact: true}).click();
  const rows = await page.getByLabel(new RegExp('^' + label + ' action \\d+$')).count();
  await page.getByLabel(label + ' action ' + rows, {exact: true}).selectOption(g.action + '|' + g.resource_kind);
  await page.getByLabel(label + ' selection ' + rows, {exact: true}).selectOption(g.selector);
  if (g.selector === 'include') await page.getByLabel(label + ' IDs ' + rows, {exact: true}).fill(g.ids.join(', '));
}
export const include = (action: string, resource_kind = 'project', ids = ['payment-service']): Grant => ({action, resource_kind, ids, selector: 'include'});
export async function goto(page: Page, section: string) {await page.goto((await environment()).origin + '/console/o/org-a/' + section);}
