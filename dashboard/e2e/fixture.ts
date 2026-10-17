import { test as base, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { readFile, writeFile, rename, unlink } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import path from 'node:path';

interface Environment {fixture: string; origin: string; requests: string; password: string; provider_password: string}
export async function environment(): Promise<Environment> {
  const descriptor = process.env.V6_BROWSER_DESCRIPTOR; if (!descriptor) throw new Error('No isolated environment');
  const value: Environment = JSON.parse(await readFile(descriptor, 'utf8'));
  if (value.fixture !== 'acp-phase9-disposable' || !value.origin.startsWith('https://127.0.0.1:')) throw new Error('Refusing non-disposable browser environment');
  return value;
}
export async function control<T = string>(action: string, data: Record<string, unknown> = {}): Promise<T> {
  const env = await environment(); const file = path.join(env.requests, randomUUID());
  await writeFile(file + '.pending', JSON.stringify({action, ...data}), {mode: 0o600});
  await rename(file + '.pending', file + '.request');
  for (let attempt = 0; attempt < (action === 'knowledge' ? 3600 : 900); attempt++) {
    try {
      const answer: {error?: string; result: T} = JSON.parse(await readFile(file + '.reply', 'utf8'));
      await unlink(file + '.reply');
      if (answer.error) throw new Error(`Fixture operation ${action}: ${answer.error}`);
      return answer.result;
    } catch (error) {if (!(typeof error === 'object' && error !== null && 'code' in error && error.code === 'ENOENT')) throw error;}
    await delay(50);
  }
  throw new Error('Timed out waiting for bounded fixture operation: ' + action);
}
export const test = base.extend<{isolated: void}>({
  isolated: [async ({}, use) => {await control('reset'); await use();}, {auto: true}],
});
export { expect };
export async function open(page: Page, url = '/console/sign-in') {await page.goto((await environment()).origin + url);}
export async function localLogin(page: Page, user = 'member', index = 0, password?: string) {
  await open(page);
  await page.getByLabel('Organization ID', {exact: true}).fill('org-a');
  await page.getByLabel('Email address', {exact: true}).fill(user + '@example.test');
  await page.getByLabel('Password', {exact: true}).fill(password ?? (await environment()).password);
  await page.getByRole('button', {name: 'Sign in', exact: true}).click();
  await page.getByLabel('Use a recovery code', {exact: true}).check();
  await page.getByLabel('Recovery code', {exact: true}).fill(await control('recovery', {user, index}));
  await page.getByRole('button', {name: 'Verify and sign in'}).click();
  await expect(page.getByRole('heading', {name: 'Welcome, ' + user})).toBeVisible();
}
export async function providerLogin(page: Page, username = 'alice') {
  await page.locator('#username').fill(username);
  await page.locator('#password').fill((await environment()).provider_password);
  await page.locator('#kc-login').click();
  // Realm B requires real provider OTP; realm A intentionally supplies no trusted MFA.
  const state = await Promise.race([page.locator('#otp').waitFor({state: 'visible'}).then(() => 'otp'), page.waitForURL('**/console/**').then(() => 'complete')]);
  if (state === 'otp') {
    await page.locator('#otp').fill(await control('provider-totp'));
    await page.locator('#kc-login').click();
  }
  await page.waitForURL('**/console/**');
}
export async function noStoredCredentials(page: Page) {
  expect(await page.evaluate(() => ({local: {...localStorage}, session: {...sessionStorage}}))).toEqual({local: {}, session: {}});
  expect(await page.evaluate(() => document.cookie)).not.toContain('__Host-acp');
  const cookies = await page.context().cookies((await environment()).origin);
  for (const cookie of cookies.filter(cookie => cookie.name.startsWith('__Host-acp'))) {
    expect(cookie.httpOnly).toBe(true); expect(cookie.secure).toBe(true); expect(cookie.sameSite).toBe('Lax'); expect(cookie.path).toBe('/');
  }
}
