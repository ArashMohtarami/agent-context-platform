import { defineConfig } from '@playwright/test';
import base from './playwright.config';

if (!(process.argv.includes('--list') && process.env.ACP_FOCUSED_PLAN_ONLY === '1')
    && !process.env.ACP_FOCUSED_RUN_ID) throw new Error('Use the explicit focused planner/runner');

// Selection is mandatory in the Python manifest. Global setup refuses to spawn
// the broad legacy wrappers when ACP_FOCUSED_RUN_ID is supplied.
export default defineConfig({
  ...base,
  reporter: [['json', {outputFile: process.env.ACP_FOCUSED_BROWSER_REPORT}]],
  outputDir: process.env.ACP_FOCUSED_BROWSER_OUTPUT ?? 'test-results/focused',
  use: {...base.use, ignoreHTTPSErrors: false, screenshot: 'off'},
});
