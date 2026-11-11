import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  workers: 1,
  retries: 0,
  forbidOnly: true,
  timeout: 90_000,
  globalTimeout: 240_000,
  reporter: 'list',
  use: {
    baseURL: process.env.DOCKET_BROWSER_URL,
    headless: true,
    channel: 'chromium',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
