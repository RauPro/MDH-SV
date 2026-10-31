import { expect, test } from '@playwright/test';
import { dossierBackedReference, formatViolations, runAxe } from './helpers';

// Spec 11: axe-core on the home page, a dossier dialog, the archive view and
// the statistics view. Only serious and critical violations fail the suite.
test.describe('accessibility', () => {
  test('home page', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.archive-record').first()).toBeVisible();
    const violations = await runAxe(page);
    expect(violations, formatViolations(violations)).toEqual([]);
  });

  test('news dossier dialog', async ({ page }) => {
    const reference = dossierBackedReference();
    await page.goto('/');
    await page.getByRole('button', { name: reference.title }).first().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    const violations = await runAxe(page);
    expect(violations, formatViolations(violations)).toEqual([]);
  });

  test('archive view', async ({ page }) => {
    await page.goto('/#archivo');
    await expect(page.locator('details.archive-coverage')).toBeVisible();
    const violations = await runAxe(page);
    expect(violations, formatViolations(violations)).toEqual([]);
  });

  test('statistics view', async ({ page }) => {
    await page.goto('/#estadisticas');
    await expect(page.locator('.official-statistics')).toBeVisible();
    const violations = await runAxe(page);
    expect(violations, formatViolations(violations)).toEqual([]);
  });
});
