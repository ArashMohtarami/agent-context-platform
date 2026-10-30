import { defineConfig } from '@playwright/test';
import { createHash, X509Certificate } from 'node:crypto';
import { readFileSync, realpathSync } from 'node:fs';
import path from 'node:path';
import base from './playwright.config';

if (!(process.argv.includes('--list') && process.env.ACP_FOCUSED_PLAN_ONLY === '1')
    && !process.env.ACP_FOCUSED_RUN_ID) throw new Error('Use the explicit focused planner/runner');

const args: string[] = [];
if (process.env.ACP_FOCUSED_RUN_ID) {
  const state = process.env.ACP_FOCUSED_STATE;
  if (!state) throw new Error('Missing owned fixture state');
  const certificatePath = path.join(realpathSync(state), 'oidc/server.crt');
  if (realpathSync(certificatePath) !== certificatePath) throw new Error('Refusing foreign fixture certificate');
  const certificate = new X509Certificate(readFileSync(certificatePath));
  if (certificate.checkIP('127.0.0.1') !== '127.0.0.1') throw new Error('Fixture certificate requires loopback SAN');
  const spki = certificate.publicKey.export({type: 'spki', format: 'der'});
  // Trust only this freshly generated fixture key in Playwright's temporary
  // Chromium profile. Node separately verifies the fixture CA; no system CA edit.
  args.push('--ignore-certificate-errors-spki-list=' + createHash('sha256').update(spki).digest('base64'));
}

// Selection is mandatory in the Python manifest. Global setup refuses to spawn
// the broad legacy wrappers when ACP_FOCUSED_RUN_ID is supplied.
export default defineConfig({
  ...base,
  reporter: [['json', {outputFile: process.env.ACP_FOCUSED_BROWSER_REPORT}]],
  outputDir: process.env.ACP_FOCUSED_BROWSER_OUTPUT ?? 'test-results/focused',
  use: {...base.use, ignoreHTTPSErrors: false, screenshot: 'off',
    launchOptions: {...base.use?.launchOptions, args}},
});
