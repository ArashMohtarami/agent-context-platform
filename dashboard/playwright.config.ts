import { defineConfig } from '@playwright/test';
import { tmpdir } from 'node:os';
import path from 'node:path';

process.env.V6_BROWSER_DESCRIPTOR ??= path.join(tmpdir(), `acp-dashboard-browser-${process.pid}.json`);
export default defineConfig({
  testDir: './e2e', forbidOnly: true, fullyParallel: false, workers: 1, retries: 0, timeout: 90000,
  expect: {timeout: 12000},
  globalSetup: './e2e/setup.ts',
  reporter: [['list'], ['junit', {outputFile: 'test-results/browser.xml'}]],
  use: {browserName: 'chromium', ignoreHTTPSErrors: true, trace: 'off', screenshot: 'only-on-failure',
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? {executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE} : {},
    viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'},
});
