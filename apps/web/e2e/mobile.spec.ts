import { expect, test } from '@playwright/test';
import { consoleErrors, dossierBackedReference, externalRequests } from './helpers';

// Spec 8: 390×844 mobile smoke — the home render (spec 1 essentials) and the
// dossier dialog (spec 3) on a phone-sized viewport.
test.describe('mobile smoke', () => {
  test('home renders without errors or external requests', async ({ page }) => {
    const errors = consoleErrors(page);
    const external = externalRequests(page);
    await page.goto('/');
    await expect(page.locator('.archive-record').first()).toBeVisible();
    await expect(page.locator('.territory-map')).toBeVisible();
    await page.waitForLoadState('networkidle');
    expect(errors).toEqual([]);
    expect(external).toEqual([]);
  });

  test('dossier dialog opens, closes with Escape and restores focus', async ({ page }) => {
    const reference = dossierBackedReference();
    await page.goto('/');
    const opener = page.getByRole('button', { name: reference.title }).first();
    await expect(opener).toBeVisible();
    await opener.click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
  });
});
