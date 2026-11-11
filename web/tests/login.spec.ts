import { test, expect } from '@playwright/test';

for (const resource of ['review', 'style', 'script']) {
  for (const failure of ['401', '503', 'network']) {
    test(`saved reviewer token after ${resource} ${failure}`, async ({ page }) => {
      const token = process.env.DOCKET_REVIEW_TOKEN!;
      await page.addInitScript(value => sessionStorage.setItem('docket-review-token', value), token);
      const path = resource === 'review' ? '**/review' : resource === 'style' ? '**/assets/*.css' : '**/assets/*.js';
      await page.route(path, async route => {
        expect(route.request().headers()['authorization']).toBe('Bearer ' + token);
        if (failure === 'network') await route.abort('failed');
        else await route.fulfill({ status: Number(failure), body: 'Temporary failure' });
      });
      await page.goto('/');
      await expect(page.getByRole('button', { name: 'Unlock review' })).toBeEnabled();
      await expect(page.getByRole('status')).not.toHaveText('Checking access…');
      expect(await page.evaluate(() => sessionStorage.getItem('docket-review-token')))
        .toBe(failure === '401' ? null : token);
      await expect(page.getByRole('navigation', { name: 'Review queue' })).toHaveCount(0);
      if (failure !== '401') {
        await page.unroute(path);
        await page.getByLabel('Reviewer token').fill(token);
        await page.getByRole('button', { name: 'Unlock review' }).click();
        await expect(page.getByRole('navigation', { name: 'Review queue' })).toBeVisible();
        expect(await page.evaluate(() => sessionStorage.getItem('docket-review-token'))).toBe(token);
      }
    });
  }
}
