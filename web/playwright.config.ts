import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  // The beta stack signs in with an invite code and the local stack with the reviewer token,
  // so each run takes only the specs written for its access.
  testIgnore: process.env.DOCKET_ACCESS === 'invite' ? ['login.spec.ts', 'review.spec.ts'] : ['beta.spec.ts'],
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
