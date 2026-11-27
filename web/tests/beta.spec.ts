import { test, expect, type Browser, type Page } from '@playwright/test';

// S12: each browser opens its own private sandbox on the beta stack, with no form.
const KIND = 'Address not on file';
const DELETED = 'Deleted. The last copies in the workflow store and on disk are erased within a few minutes.';

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
  expect(await page.evaluate(() => Object.keys(sessionStorage))).toEqual([]);
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
  await expect(page.getByRole('status').first()).toHaveText(DELETED, { timeout: 60_000 });
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
  // The page's first readiness request can still be in flight when goto returns, and the route
  // below would answer it, so the real first answer must be in before the route goes on.
  await expect(send).toBeEnabled();

  // A refusal: Send stays off until the page asks again and the server says it is ready.
  // The send's refusal and the first readiness answer have different texts, so the second
  // text shows the page has taken in an answer that is not ready.
  const turnedOff = 'Live answers are turned off right now.';
  const catchingUp = 'Docket is catching up. Try again in a few minutes.';
  const asked: string[] = [];
  let serverReady = false;
  await page.route('**/beta/samples', async route => {
    if (route.request().method() === 'POST') return route.fulfill({ status: 503, json: { detail: turnedOff } });
    asked.push(route.request().method());
    if (serverReady) return route.continue();
    const options = await (await route.fetch()).json();
    return route.fulfill({ json: { ...options, state: 'catching_up' } });
  });
  await expect(send).toBeEnabled();
  await page.getByLabel('Kind of document').selectOption({ label: KIND });
  await send.click();
  await expect(page.locator('.sample-state')).toHaveText(turnedOff);
  await expect(send).toBeDisabled();
  // The first time it asks, the server is still catching up, so Send stays off.
  await page.clock.fastForward(31_000);
  await expect(page.locator('.sample-state')).toHaveText(catchingUp);
  expect(asked).toEqual(['GET']);
  await expect(send).toBeDisabled();
  serverReady = true;
  await page.clock.fastForward(31_000);
  await expect.poll(() => asked).toEqual(['GET', 'GET']);
  await expect(send).toBeEnabled();
  await expect(page.locator('.sample-state')).toHaveText('');
  await page.unroute('**/beta/samples');

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
  await expect(page.getByRole('status').first()).toHaveText(DELETED, { timeout: 60_000 });
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

test('S12: an ended or ending sandbox is handled, and one tab sends or deletes at a time', async ({ browser }) => {
  // Four samples read and three Delete nows, each allowed up to a minute.
  test.setTimeout(240_000);
  const context = await browser.newContext();
  const first = await context.newPage();
  const deletes: string[] = [];
  context.on('request', request => {
    if (request.method() === 'DELETE' && new URL(request.url()).pathname === '/sandbox') deletes.push(request.url());
  });
  await first.goto('/reviewer');
  const send = first.getByRole('button', { name: 'Send a sample document' });
  const fresh = first.getByRole('button', { name: 'Start fresh' });
  const links = first.getByRole('navigation', { name: 'Review queue' }).getByRole('link');
  const old = await sendSample(first);
  await expect(links).toHaveCount(1);

  // An answer that the sandbox ended takes its documents off the page. The answer spends the
  // cookie as the server's does, and the queue refresh after it fails, so only the page's own
  // clearing can empty the view.
  await first.route('**/beta/samples', route => route.request().method() === 'POST'
    ? route.fulfill({
      status: 409,
      json: { detail: 'That sandbox ended. Send again.', code: 'sandbox_ended' },
      headers: { 'Set-Cookie': '__Host-docket_sandbox=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0' },
    })
    : route.continue());
  await first.route('**/review', route => route.fulfill({ status: 500, json: { detail: 'Request failed' } }));
  await send.click();
  await expect(first.locator('.sample-state')).toHaveText('That sandbox ended. Send again.');
  await expect(links).toHaveCount(0);
  await expect(first.getByRole('heading', { name: 'Nothing waiting' })).toBeVisible();
  expect(new URL(first.url()).hash).toBe('');
  await expect(first.locator('.sandbox-line')).toHaveText('Private to this browser · starts with your first sample');
  await expect(send).toBeEnabled();
  await first.unroute('**/review');
  await first.unroute('**/beta/samples');

  // In its last minutes the sandbox refuses; Start fresh deletes it and sends to a new one.
  const ending = 'This sandbox ends in less than five minutes, so it takes no new samples. Start fresh to delete it and send this sample to a new one.';
  let refusals = 0;
  await first.route('**/beta/samples', route => route.request().method() === 'POST' && refusals++ === 0
    ? route.fulfill({ status: 409, json: { detail: ending, code: 'sandbox_ending' } })
    : route.continue());
  await send.click();
  await expect(first.locator('.sample-state')).toHaveText(ending);
  await expect(fresh).toBeVisible();
  expect(deletes).toEqual([]);
  await fresh.click();
  await expect(first.locator('.sample-state')).toHaveText('The sample is waiting for review in the queue.', { timeout: 60_000 });
  expect(deletes).toHaveLength(1);
  await expect(fresh).toBeHidden();
  await expect(links).toHaveCount(1);
  expect(decodeURIComponent(new URL(first.url()).hash.slice(1))).not.toBe(old);
  await first.unroute('**/beta/samples');

  // A send held at the server keeps Delete now off in its tab and holds back another tab's.
  const second = await context.newPage();
  await second.goto('/reviewer');
  await expect(second.getByRole('navigation', { name: 'Review queue' }).getByRole('link')).toHaveCount(1);
  // The send reaches the server, and its answer is held before it reaches the browser, so
  // a delete sent at any point before the answer arrives is seen as an overlap.
  let answer: () => void = () => {};
  const answered = new Promise<void>(resolve => { answer = resolve; });
  let sendAnswered = false;
  const overlaps: string[] = [];
  second.on('request', request => {
    if (request.method() === 'DELETE' && !sendAnswered) overlaps.push(request.url());
  });
  await first.route('**/beta/samples', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    const response = await route.fetch();
    await answered;
    sendAnswered = true;
    await route.fulfill({ response });
  });
  await send.click();
  await expect(first.getByRole('button', { name: 'Delete now' })).toBeDisabled();
  await second.getByRole('button', { name: 'Delete now' }).click();
  await second.waitForTimeout(1_000);
  answer();
  await expect(second.getByRole('status').first()).toHaveText(DELETED, { timeout: 60_000 });
  expect(overlaps).toEqual([]);
  expect(deletes).toHaveLength(2);
  await first.unroute('**/beta/samples');

  // And the other way: a Delete now held at the server in one tab holds back another tab's send.
  await expect(send).toBeEnabled({ timeout: 60_000 });
  await sendSample(first);
  await second.reload();
  const secondSends: boolean[] = [];
  let deleteAnswered = false;
  second.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/beta/samples') secondSends.push(deleteAnswered);
  });
  let release: () => void = () => {};
  const released = new Promise<void>(resolve => { release = resolve; });
  let deleteDone = false;
  await first.route('**/sandbox', async route => {
    if (route.request().method() !== 'DELETE') return route.continue();
    const response = await route.fetch({ timeout: 60_000 });
    deleteDone = true;
    await released;
    deleteAnswered = true;
    await route.fulfill({ response });
  });
  await first.getByRole('button', { name: 'Delete now' }).click();
  // The delete has finished at the server; only its answer is held.
  await expect.poll(() => deleteDone, { timeout: 60_000 }).toBe(true);
  expect(deletes).toHaveLength(3);
  await second.getByLabel('Kind of document').selectOption({ label: KIND });
  await second.getByRole('button', { name: 'Send a sample document' }).click();
  await second.waitForTimeout(1_000);
  release();
  await expect(first.getByRole('status').first()).toHaveText(DELETED, { timeout: 60_000 });
  await expect(second.locator('.sample-state')).toHaveText('The sample is waiting for review in the queue.', { timeout: 60_000 });
  // One send, and it left only after the delete's answer reached the browser.
  expect(secondSends).toEqual([true]);
  expect(deletes).toHaveLength(3);
  await first.unroute('**/sandbox');
  await context.close();
});

test('S12: a held document opens, previews after a reload, and its suggestion is approved', async ({ browser }) => {
  const name = process.env.DOCKET_DOCUMENT_NAME!;
  const context = await browser.newContext();
  // The sandbox the test put the document in, as the browser that sent it would hold it.
  await context.addCookies([{
    name: '__Host-docket_sandbox', value: process.env.DOCKET_SANDBOX_COOKIE!,
    domain: new URL(process.env.DOCKET_BROWSER_URL!).hostname, path: '/',
    httpOnly: true, secure: true, sameSite: 'Strict',
  }]);
  const page = await context.newPage();
  const authorized: string[] = [];
  page.on('request', request => {
    if (request.headers()['authorization'] !== undefined) authorized.push(request.url());
  });
  await page.goto('/reviewer');
  await page.getByRole('navigation', { name: 'Review queue' }).getByRole('link', { name: new RegExp(name) }).click();
  await expect(page.getByRole('heading', { name })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name })).toBeVisible();
  await expect(page.getByTitle('PDF preview')).toHaveAttribute('src', /^blob:/);
  await page.screenshot({ path: test.info().outputPath('beta-review-open.png'), fullPage: true });
  await page.getByRole('button', { name: 'Approve suggested', exact: true }).click();
  await expect(page.getByTestId('document-status')).toHaveText('Status: filed', { timeout: 60_000 });
  await page.getByText('Document trace', { exact: true }).click();
  await expect(page.locator('pre')).toContainText('review_approved');
  await page.screenshot({ path: test.info().outputPath('beta-review-filed.png'), fullPage: true });
  expect(await page.evaluate(() => Object.keys(sessionStorage))).toEqual([]);
  expect(authorized).toEqual([]);
  await context.close();
});
