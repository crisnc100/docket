import { test, expect } from '@playwright/test';

const code = process.env.DOCKET_INVITE_CODE!;
const name = process.env.DOCKET_DOCUMENT_NAME!;

test('A7: an invite code signs in and one held document is reviewed on the beta stack', async ({ page }) => {
  const authorized: string[] = [];
  page.on('request', request => {
    if (request.headers()['authorization'] !== undefined) authorized.push(request.url());
  });
  const response = await page.goto('/reviewer');
  expect(response!.status()).toBe(401);
  await expect(page.getByLabel('Reviewer token')).toHaveCount(0);
  await page.getByLabel('Invite code').fill('not-the-code-' + '0'.repeat(16));
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('status')).toHaveText('That invite code is not valid.');
  await expect(page.getByRole('navigation', { name: 'Review queue' })).toHaveCount(0);
  await page.getByLabel('Invite code').fill(code);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('navigation', { name: 'Review queue' })).toBeVisible();

  const session = (await page.context().cookies()).find(cookie => cookie.name === '__Host-docket_session');
  expect(session).toBeDefined();
  expect(session!).toMatchObject({ httpOnly: true, secure: true, sameSite: 'Strict', path: '/' });
  expect(session!.value).not.toContain(code);
  expect(await page.evaluate(() => document.cookie)).not.toContain('docket_session');
  expect(await page.evaluate(() => Object.keys(sessionStorage))).toEqual([]);
  expect(page.url()).not.toContain(code);

  // A visitor's own upload, sent by the signed-in page with its cookie and origin, is refused.
  const upload = await page.evaluate(async () => {
    const form = new FormData();
    form.append('file', new Blob(['%PDF-1.4'], { type: 'application/pdf' }), 'visitor.pdf');
    return (await fetch('/documents', { method: 'POST', body: form })).status;
  });
  expect(upload).toBe(403);

  await page.getByRole('navigation', { name: 'Review queue' }).getByRole('link', { name: new RegExp(name) }).click();
  await expect(page.getByRole('heading', { name })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name })).toBeVisible();
  await expect(page.getByLabel('Invite code')).toHaveCount(0);
  await expect(page.getByTitle('PDF preview')).toHaveAttribute('src', /^blob:/);
  await page.screenshot({ path: test.info().outputPath('beta-review-open.png'), fullPage: true });
  await page.getByRole('button', { name: 'Approve suggested', exact: true }).click();
  await expect(page.getByTestId('document-status')).toHaveText('Status: filed', { timeout: 60_000 });
  await page.getByText('Document trace', { exact: true }).click();
  await expect(page.locator('pre')).toContainText('review_approved');
  await page.screenshot({ path: test.info().outputPath('beta-review-filed.png'), fullPage: true });
  expect(authorized).toEqual([]);
});
