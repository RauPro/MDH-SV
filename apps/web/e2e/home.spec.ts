import { expect, test } from '@playwright/test';
import { consoleErrors, cspViolations, externalRequests, newsArchive } from './helpers';

// Spec 1: the news monitor home renders cards, map and processing notes with
// no console errors, no CSP violations and no external requests.
test.describe('news monitor home', () => {
  test('renders cards, map and processing notes cleanly', async ({ page }) => {
    const errors = consoleErrors(page);
    const readViolations = await cspViolations(page);
    const external = externalRequests(page);
    await page.goto('/');

    const archive = newsArchive();
    const cards = page.locator('.archive-record');
    await expect(cards.first()).toBeVisible();
    expect(await cards.count()).toBeGreaterThan(0);

    await expect(page.locator('.territory-map')).toBeVisible();
    expect(await page.locator('.map-departments button').count()).toBeGreaterThan(5);

    const progress = page.locator(
      archive.policy.selection_gate.version === 1
        ? '.filter-note'
        : '.news-classification-progress',
    );
    await expect(progress.first()).toBeVisible();

    await page.waitForLoadState('networkidle');
    expect(errors).toEqual([]);
    expect(await readViolations()).toEqual([]);
    expect(external).toEqual([]);
  });
});
