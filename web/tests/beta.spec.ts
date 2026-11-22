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

test('S12: Send waits for Delete now, a sandbox that is gone leaves nothing, and a refusal clears', async ({ browser }) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.clock.install();
  await page.goto('/reviewer');
  const send = page.getByRole('button', { name: 'Send a sample document' });
  const queueLinks = page.getByRole('navigation', { name: 'Review queue' }).getByRole('link');

  // A refusal: Send stays off until the page asks again and the server is ready.
  await page.route('**/beta/samples', route => route.request().method() === 'POST'
    ? route.fulfill({ status: 503, json: { detail: 'Docket is catching up. Try again in a few minutes.' } })
    : route.continue());
  await expect(send).toBeEnabled();
  await page.getByLabel('Kind of document').selectOption({ label: KIND });
  await send.click();
  await expect(page.locator('.sample-state')).toHaveText('Docket is catching up. Try again in a few minutes.');
  await expect(send).toBeDisabled();
  await page.unroute('**/beta/samples');
  await page.clock.fastForward(31_000);
  await expect(send).toBeEnabled();
  await expect(page.locator('.sample-state')).toHaveText('');

  // While Delete now waits for its answer, Send is off; and a refresh that fails after it
  // leaves the answer and an empty page.
  const id = await sendSample(page);
  await expect(queueLinks).toHaveCount(1);
  let answer: () => void = () => {};
  const answered = new Promise<void>(resolve => { answer = resolve; });
  await page.route('**/sandbox', async route => {
    if (route.request().method() !== 'DELETE') return route.continue();
    await answered;
    await route.continue();
  });
  await page.getByRole('button', { name: 'Delete now' }).click();
  await expect(send).toBeDisabled();
  await page.route('**/review', route => route.fulfill({ status: 500, json: { detail: 'Request failed' } }));
  answer();
  await expect(page.getByRole('status').first()).toHaveText('Everything this browser sent is deleted.');
  await expect(send).toBeEnabled();
  await expect(queueLinks).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Nothing waiting' })).toBeVisible();
  expect(new URL(page.url()).hash).toBe('');
  await page.unroute('**/review');
  await page.unroute('**/sandbox');

  // At the end of its life the page clears the old document itself, even while the server,
  // whose clock has not moved, would still show it.
  const second = await sendSample(page);
  expect(second).not.toBe(id);
  await expect(page.locator('.sandbox-line')).toContainText('deleted in');
  await page.route('**/review', route => route.fulfill({ json: { items: [] } }));
  await page.route(`**/documents/${second}*`, route => route.fulfill({ status: 404, json: { detail: 'Unknown document' } }));
  await page.clock.fastForward('51:00');
  await expect(page.getByRole('heading', { name: 'Nothing waiting' })).toBeVisible();
  await expect(queueLinks).toHaveCount(0);
  await expect(page.locator('.sandbox-line')).toHaveText('Private to this browser · starts with your first sample');
  expect(new URL(page.url()).hash).toBe('');
  await expect(page.locator('iframe, object, embed')).toHaveCount(0);
  await context.close();
});
