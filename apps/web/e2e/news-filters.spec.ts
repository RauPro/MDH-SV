import { expect, test } from '@playwright/test';
import { newsArchive, queryMatches, selectedRecords } from './helpers';

// Spec 2: a query narrows the results, the publisher filter constrains every
// visible card, an invalid date range shows the alert, and reset restores the
// full list. Counts are derived from archive/news.json, not hard-coded.
test.describe('news search and filters', () => {
  test('query, publisher filter, invalid dates and reset', async ({ page }) => {
    const archive = newsArchive();
    const total = selectedRecords().length;
    const status = page.locator('.archive-results [role="status"]');

    await page.goto('/');
    await expect(page.locator('.archive-record').first()).toBeVisible();
    await expect(status).toContainText(`${total} noticia`);

    // A distinctive title word narrows the result set to its exact matches.
    const reference = archive.records[0];
    const needle = reference.title
      .split(/\s+/)
      .map(word => word.replaceAll(/[^\p{L}\p{N}]/gu, ''))
      .find(word => word.length > 6 && queryMatches(word).length < total)
      ?? archive.coverage.sources[0].publisher;
    const expected = queryMatches(needle).length;
    expect(expected).toBeGreaterThan(0);
    await page.locator('.search-box input[type="search"]').fill(needle);
    await expect(status).toContainText(`${expected} noticia`);

    // The publisher filter constrains every visible card.
    await page.locator('.search-box input[type="search"]').fill('');
    const publisher = reference.publisher;
    const publisherCount = selectedRecords().filter(r => r.publisher === publisher).length;
    await page
      .locator('label:has-text("Medio") select')
      .selectOption({ label: publisher });
    await expect(status).toContainText(`${publisherCount} noticia`);
    for (const text of await page.locator('.archive-record .record-category').allTextContents()) {
      expect(text).toBe(publisher);
    }

    // An invalid date range shows the alert instead of results.
    await page.locator('label:has-text("Publicación desde") input').fill('2025-12-31');
    await page.locator('label:has-text("Publicación hasta") input').fill('2025-01-01');
    await expect(page.getByRole('alert')).toBeVisible();

    // Reset restores the unfiltered list.
    await page.getByRole('button', { name: 'Restablecer filtros de noticias' }).click();
    await expect(status).toContainText(`${total} noticia`);
  });
});
