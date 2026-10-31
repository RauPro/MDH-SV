import { defineConfig, devices } from '@playwright/test';

// scripts/test-browser.mjs starts the preview replica on an ephemeral port and
// exports its URL here, so the config stays synchronous across the several
// evaluations Playwright performs (runner process plus each worker).
const baseURL = process.env.MDH_PREVIEW_URL;
if (!baseURL) {
  throw new Error('MDH_PREVIEW_URL is not set; run npm run test:browser instead of playwright test directly.');
}

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  reporter: [
    [process.env.CI ? 'github' : 'list'],
    ['html', { open: 'never', outputFolder: 'playwright-report' }],
  ],
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'] },
      testIgnore: 'mobile.spec.ts',
    },
    {
      name: 'mobile',
      use: { ...devices['Desktop Chrome'], viewport: { width: 390, height: 844 } },
      testMatch: 'mobile.spec.ts',
    },
  ],
});
