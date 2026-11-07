import { expect, test } from '@playwright/test';
import { consoleErrors, cspViolations, externalRequests, newsArchive } from './helpers';

// Spec 1: the news monitor home renders cards, map and processing notes with
// no console errors, no CSP violations and no external requests.
test.describe('news monitor home', () => {
  test('keeps the map and compact department cards aligned without a tall blank column', async ({ page }) => {
    await page.goto('/');
    const map = page.getByRole('region', { name: 'Mapa de El Salvador' });
    await expect(map.getByRole('button', { name: /^San Salvador/ })).toContainText('Oficial');
    await map.scrollIntoViewIfNeeded();
    const layout = await map.locator('.map-layout').boundingBox();
    const canvas = await map.locator('.map-canvas').boundingBox();
    const panel = await map.boundingBox();
    expect(layout!.height).toBeLessThan(520);
    expect(canvas!.y - panel!.y).toBeLessThan(240);
    expect(await map.locator('.map-departments button').count()).toBe(14);
    for (const card of await map.locator('.map-departments button').all()) {
      const bounds = (await card.boundingBox())!;
      expect(bounds.height).toBeGreaterThanOrEqual(44);
      expect(bounds.height).toBeLessThan(110);
    }
    await map.getByText('Cómo leer el mapa y sus fuentes').click();
    await expect(map.getByText(/Cada capa conserva su unidad/)).toBeVisible();
  });

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
