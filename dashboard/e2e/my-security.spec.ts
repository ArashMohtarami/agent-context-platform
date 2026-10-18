import AxeBuilder from '@axe-core/playwright';
import { control, environment, expect, localLogin, noStoredCredentials, open, providerLogin, test } from './fixture';

test('one-time recovery replacement and own browser-session revocation are real', async ({page}) => {
  await localLogin(page);
  await page.getByRole('link', {name: 'My security', exact: true}).click();
  await page.getByLabel('Fresh authenticator code', {exact: true}).fill(await control('totp'));
  await page.getByRole('button', {name: 'Replace recovery codes'}).click();
  const modal = page.getByRole('dialog', {name: 'Save your recovery codes'});
  await expect(modal.locator('li')).toHaveCount(10);
  await modal.getByRole('button', {name: 'I saved these codes'}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button', {name: 'Revoke this browser session'}).click();
  await page.getByRole('button', {name: 'Revoke session', exact: true}).click();
  await expect(page.getByText('This browser session was revoked.')).toBeVisible();
  await noStoredCredentials(page);
});

test('password change signs out, and stale MFA requires fresh verification', async ({page}) => {
  await localLogin(page);
  await control('stale-mfa');
  await page.reload();
  await page.getByRole('link', {name: 'My security', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Change password'})).toBeDisabled();
  await page.getByLabel('Use a recovery code', {exact: true}).check();
  await page.getByLabel('Authentication or recovery code', {exact: true}).fill(await control('recovery', {index: 1}));
  await page.getByRole('button', {name: 'Verify identity', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Change password'})).toBeEnabled();
  await page.getByLabel('Current password', {exact: true}).fill((await environment()).password);
  await page.getByLabel('New password', {exact: true}).fill('fictional-changed-browser-password');
  await page.getByRole('button', {name: 'Change password', exact: true}).click();
  await expect(page.getByText('Password changed. Sign in again with MFA.')).toBeVisible();
  await localLogin(page, 'member', 2, 'fictional-changed-browser-password');
});

test('CSRF failure is not replayed and the form can be safely resubmitted', async ({page}) => {
  await localLogin(page);
  await page.getByRole('link', {name: 'My security', exact: true}).click();
  await page.route('**/api/v6/auth/mfa/recovery', async route => {
    await route.continue({headers: {...route.request().headers(), 'x-csrf-token': 'invalid'}});
  }, {times: 1});
  await page.getByLabel('Fresh authenticator code', {exact: true}).fill(await control('totp'));
  await page.getByRole('button', {name: 'Replace recovery codes'}).click();
  await expect(page.getByText('The security check failed. Review and submit again.')).toBeVisible();
  await expect(page.getByRole('dialog', {name: 'Save your recovery codes'})).toHaveCount(0);
  await page.getByLabel('Fresh authenticator code', {exact: true}).fill(await control('totp'));
  await page.getByRole('button', {name: 'Replace recovery codes'}).click();
  await expect(page.getByRole('dialog', {name: 'Save your recovery codes'})).toBeVisible();
});

test('linking proves the local identity and real provider, then invalidates the old session', async ({page}) => {
  await localLogin(page, 'lead');
  expect(await control<number>('identity-count')).toBe(1);
  await page.getByRole('link', {name: 'My security', exact: true}).click();
  await page.getByLabel('Organization provider to link').selectOption('org-a');
  await page.getByRole('button', {name: 'Link a verified identity'}).click();
  await providerLogin(page, 'other');
  await expect(page.getByText('Login identity linked. Sign in again to verify your current access.')).toBeVisible();
  expect(await control<number>('identity-count')).toBe(2);
  await noStoredCredentials(page);
});

test('responsive light/dark specimens, keyboard focus, reduced motion and zoom reflow', async ({page}, testInfo) => {
  await open(page, '/console/specimens');
  for (const width of [360, 768, 1440, 720]) {
    await page.setViewportSize({width, height: 1000});
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => {document.documentElement.dataset.theme = theme;}, theme);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(await page.locator('img').evaluateAll(images => images.every(image => image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0 && !image.src.startsWith('data:')))).toBe(true);
      const audit = await new AxeBuilder({page}).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
      expect(audit.violations.map(violation => ({id: violation.id, nodes: violation.nodes.map(node => node.target)}))).toEqual([]);
      await page.screenshot({path: testInfo.outputPath(`specimens-${width}-${theme}.png`), fullPage: true});
    }
  }
  await page.getByRole('button', {name: 'Open example dialog'}).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Tab');
  expect(await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))).toBe(true);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', {name: 'Open example dialog'})).toBeFocused();
  await page.emulateMedia({forcedColors: 'active', reducedMotion: 'reduce'});
  await expect(page.getByRole('heading', {name: 'Interface foundations'})).toBeVisible();
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Emulation.setDeviceMetricsOverride', {width: 720, height: 500, deviceScaleFactor: 2, mobile: false});
  expect(await page.evaluate(() => ({width: innerWidth, ratio: devicePixelRatio}))).toEqual({width: 720, ratio: 2});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: testInfo.outputPath('specimens-200-percent-equivalent.png'), fullPage: true});
  await cdp.detach();
  // This reproduces 1440 physical pixels / 720 CSS pixels at 200% browser zoom.
  // Browser-toolbar gestures and OS display scaling are not emulated.
});

test('real login and security screens reflow in both themes without fake data', async ({page}, testInfo) => {
  await open(page);
  for (const width of [360, 768, 1440]) {
    await page.setViewportSize({width, height: 1000});
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => {document.documentElement.dataset.theme = theme;}, theme);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(await page.locator('img').evaluateAll(images => images.every(image => image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0 && !image.src.startsWith('data:')))).toBe(true);
      expect((await new AxeBuilder({page}).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([]);
      await page.screenshot({path: testInfo.outputPath(`login-${width}-${theme}.png`), fullPage: true});
    }
  }
  await localLogin(page);
  await page.getByRole('link', {name: 'My security', exact: true}).click();
  for (const width of [360, 768, 1440, 720]) {
    await page.setViewportSize({width, height: 1000});
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => {document.documentElement.dataset.theme = theme;}, theme);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(await page.locator('img').evaluateAll(images => images.every(image => image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0 && !image.src.startsWith('data:')))).toBe(true);
      const audit = await new AxeBuilder({page}).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
      expect(audit.violations.map(violation => ({id: violation.id, nodes: violation.nodes.map(node => node.target)}))).toEqual([]);
      await page.screenshot({path: testInfo.outputPath(`security-${width}-${theme}.png`), fullPage: true});
    }
  }
});


test('revoking another own human browser session refuses its next private read', async ({page, browser}) => {
  await localLogin(page);
  const other = await browser.newContext({ignoreHTTPSErrors: true});
  try {
    const second = await other.newPage(); await localLogin(second, 'member', 1);
    await page.getByRole('link', {name: 'My security', exact: true}).click();
    await page.getByRole('button', {name: 'Revoke browser session', exact: true}).click();
    await page.getByRole('button', {name: 'Revoke session', exact: true}).click();
    await expect(page.getByText('Browser session revoked.', {exact: true})).toBeVisible();
    await second.getByRole('link', {name: 'My access', exact: true}).click();
    await expect(second.getByRole('heading', {name: 'Your context, connected.'})).toBeVisible();
    await expect(second.getByText('Your session ended. Sign in again to continue.')).toBeVisible();
    await expect(page.getByRole('heading', {name: 'My security', exact: true})).toBeVisible();
  } finally {await other.close();}
});
