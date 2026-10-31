import { expect, test } from '@playwright/test';
import { consoleErrors } from './helpers';

// Spec 6: the official statistics view renders without errors.
test.describe('official statistics view', () => {
  test('renders the statistics workspace', async ({ page }) => {
    const errors = consoleErrors(page);
    await page.goto('/#estadisticas');
    await expect(page.locator('.official-statistics')).toBeVisible();
    await expect(page.locator('.official-statistics-section').first()).toBeVisible();
    await page.waitForLoadState('networkidle');
    expect(errors).toEqual([]);
  });
});
