import { test, expect, APIRequestContext } from '@playwright/test';
import { readFileSync } from 'node:fs';

async function submit(request: APIRequestContext, source: string, name: string, sender: string) {
  const response = await request.post('/documents', {
    multipart: {
      sender,
      file: { name, mimeType: 'application/pdf', buffer: readFileSync(`../corpus/seed-11/${source}`) },
    },
  });
  expect(response.ok()).toBeTruthy();
  const { document_id: id } = await response.json();
  await expect.poll(async () => (await (await request.get(`/documents/${id}`)).json()).status,
    { timeout: 60_000 }).toBe('needs_review');
  return id as string;
}

test('D13: queue → document → approve suggested → filed on compose replay', async ({ page, request }) => {
  const name = 'doc-0075-decoy_near_miss_address.pdf';
  const id = await submit(request, name, name, 'reviewer@example.test');
  await page.goto('/');
  await page.getByRole('navigation', { name: 'Review queue' }).getByRole('link', { name: new RegExp(name) }).click();
  await expect(page.getByRole('heading', { name: name })).toBeVisible();
  const preview = page.getByTitle('PDF preview');
  await expect(preview).toHaveAttribute('src', `/documents/${id}/file`);
  const file = await request.get(`/documents/${id}/file`);
  expect(file.headers()['content-type']).toBe('application/pdf');
  expect(file.headers()['content-disposition']).toBe('inline');
  expect((await file.body()).subarray(0, 5).toString()).toBe('%PDF-');
  await expect(page.getByRole('heading', { name: 'Suggested', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Rules top candidate' })).toBeVisible();
  const queue = (await (await request.get('/review')).json()).items;
  const suggestion = queue.find((item: {document_id: string}) => item.document_id === id).suggested_record_id;
  expect(suggestion).toBeTruthy();
  expect(suggestion).not.toBe(queue.find((item: {document_id: string}) => item.document_id === id).rules_top_record_id);
  await expect.poll(() => page.frames().filter(frame => frame.url().startsWith('chrome-extension://')).length).toBeGreaterThan(0);
  const viewer = page.frames().find(frame => frame.url().startsWith('chrome-extension://'))!;
  await expect(viewer.locator('pdf-viewer')).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('review-open.png'), fullPage: true });
  await page.getByRole('button', { name: 'Approve suggested', exact: true }).click();
  await expect(page.getByTestId('document-status')).toHaveText('Status: filed', { timeout: 60_000 });
  const trace = await (await request.get(`/documents/${id}`)).json();
  expect(trace.record_id).toBe(suggestion);
  expect(trace.reason).toBe('review_approved');
  expect((await request.post(`/review/${id}/approve`, { data: { record_id: suggestion } })).status()).toBe(409);
  await page.getByText('Document trace', { exact: true }).click();
  await expect(page.locator('pre')).toContainText('review_approved');
  await page.screenshot({ path: test.info().outputPath('review-filed.png'), fullPage: true });
});

test('D17: script filename and sender render as text without execution', async ({ page, request }) => {
  const filename = '<script>window.__docketXss=1</script>.pdf';
  const sender = '<script>window.__docketXss=2</script>';
  const id = await submit(request, 'doc-0004-tenant_only.pdf', filename, sender);
  await page.goto('/');
  const link = page.getByRole('navigation', { name: 'Review queue' }).getByRole('link').filter({ hasText: filename });
  await expect(link).toContainText(sender);
  await link.click();
  await expect(page.getByRole('heading', { name: filename, exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Document', exact: true })).toContainText(`Sender: ${sender}`);
  expect(await page.evaluate(() => (window as unknown as Record<string, unknown>).__docketXss)).toBeUndefined();
  await expect(page.locator('nav script, section script')).toHaveCount(0);
  await page.screenshot({ path: test.info().outputPath('review-xss-text.png'), fullPage: true });
  await page.getByLabel('Dismiss reason').fill('XSS test complete');
  await page.getByRole('button', { name: 'Dismiss', exact: true }).click();
  await expect(page.getByTestId('document-status')).toHaveText('Status: dismissed', { timeout: 60_000 });
  expect((await (await request.get(`/documents/${id}`)).json()).reason).toBe('XSS test complete');
});
