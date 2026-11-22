import { test, expect, type Browser, type Page } from '@playwright/test';

// S12: each browser opens its own private sandbox on the beta stack, with no form.
const KIND = 'Address not on file';

async function openDocket(browser: Browser) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const sent: string[] = [];
  const authorized: string[] = [];
  page.on('request', request => {
    if (request.method() === 'POST' && request.url().endsWith('/beta/samples')) sent.push(request.url());
    if (request.headers()['authorization'] !== undefined) authorized.push(request.url());
  });
  const response = await page.goto('/reviewer');
  expect(response!.status()).toBe(200);
  await expect(page.getByRole('navigation', { name: 'Review queue' })).toBeVisible();
  return { context, page, sent, authorized };
}

async function sendSample(page: Page) {
  await expect(page.getByRole('button', { name: 'Send a sample document' })).toBeEnabled();
  await page.getByLabel('Kind of document').selectOption({ label: KIND });
  await page.getByRole('button', { name: 'Send a sample document' }).click();
  await expect(page.locator('.sample-state')).toHaveText('The sample is waiting for review in the queue.', { timeout: 60_000 });
  return decodeURIComponent(new URL(page.url()).hash.slice(1));
}

test('S12: two browsers stay apart, a sample is reviewed, and Delete now empties the page', async ({ browser }) => {
  const first = await openDocket(browser);
  const second = await openDocket(browser);
  const { page } = first;
  await expect(page.getByLabel('Reviewer token')).toHaveCount(0);
  await expect(page.locator('.sandbox-line')).toHaveText('Private to this browser · starts with your first sample');
  expect(await page.context().cookies()).toEqual([]);

  const id = await sendSample(page);
  const [cookie] = await first.context.cookies();
  expect(cookie).toMatchObject({ name: '__Host-docket_sandbox', httpOnly: true, secure: true, sameSite: 'Strict', path: '/' });
  expect(await page.evaluate(() => document.cookie)).toBe('');
  await expect(page.locator('.sandbox-line')).toHaveText(/^Private to this browser · deleted in (49|50) minutes · Delete now$/);
  await expect(page.getByRole('navigation', { name: 'Review queue' }).getByRole('link')).toHaveCount(1);

  // The second browser sees none of it, even with the document's id.
  await second.page.goto('/reviewer#' + id);
  await expect(second.page.getByRole('navigation', { name: 'Review queue' }).getByRole('link')).toHaveCount(0);
  await expect(second.page.getByRole('status').first()).toHaveText('Unknown document');
  expect((await second.context.cookies()).length).toBe(0);

  await page.getByLabel('Dismiss reason').fill('Not one of ours.');
  await page.getByRole('button', { name: 'Dismiss', exact: true }).click();
  await expect(page.getByTestId('document-status')).toHaveText('Status: dismissed', { timeout: 60_000 });
  await page.screenshot({ path: test.info().outputPath('beta-sandbox-dismissed.png'), fullPage: true });

  await sendSample(page);
  await page.getByRole('button', { name: 'Delete now' }).click();
  await expect(page.getByRole('status').first()).toHaveText('Everything this browser sent is deleted.');
  await expect(page.getByRole('navigation', { name: 'Review queue' }).getByRole('link')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Nothing waiting' })).toBeVisible();
  await expect(page.locator('.sandbox-line')).toHaveText('Private to this browser · starts with your first sample');
  expect(await first.context.cookies()).toEqual([]);
  await page.reload();
  await expect(page.getByRole('navigation', { name: 'Review queue' }).getByRole('link')).toHaveCount(0);
  await page.screenshot({ path: test.info().outputPath('beta-sandbox-deleted.png'), fullPage: true });
  expect(second.sent).toEqual([]);
  // The cookie is the only access: no request carries a Bearer token.
  expect([...first.authorized, ...second.authorized]).toEqual([]);
  await first.context.close();
  await second.context.close();
});

test('S12: with cookies blocked the page explains and sends nothing', async ({ browser }) => {
  const context = await browser.newContext();
  // What a browser that blocks this site's cookies shows the page.
  await context.addInitScript(() => {
    Object.defineProperty(Navigator.prototype, 'cookieEnabled', { get: () => false });
    Object.defineProperty(Document.prototype, 'cookie', { get: () => '', set: () => {} });
  });
  const page = await context.newPage();
  const sent: string[] = [];
  page.on('request', request => {
    if (request.method() !== 'GET') sent.push(request.method() + ' ' + request.url());
  });
  await page.goto('/reviewer');
  await expect(page.locator('.sample-state')).toHaveText(
    'Your sandbox needs a cookie to stay private to this browser, and this browser blocks cookies here. Allow cookies for this site and reload to send a sample.');
  await expect(page.getByRole('button', { name: 'Send a sample document' })).toBeHidden();
  await expect(page.locator('.sandbox-line')).toBeHidden();
  await page.screenshot({ path: test.info().outputPath('beta-cookies-blocked.png'), fullPage: true });
  expect(sent).toEqual([]);
  await context.close();
});
