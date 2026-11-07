import { expect, test } from '@playwright/test';
import { consoleErrors, dossierBackedReference, externalRequests } from './helpers';

// Spec 8: 390×844 mobile smoke — the home render (spec 1 essentials) and the
// dossier dialog (spec 3) on a phone-sized viewport.
test.describe('mobile smoke', () => {
  test('contains the historical chart overflow and keeps every year keyboard-accessible', async ({ page }) => {
    await page.goto('/');
    const chart = page.getByRole('region', { name: 'Homicidios reportados por fuentes oficiales' });
    const years = chart.getByRole('group', { name: 'Totales nacionales por año' });
    const last = years.getByRole('button').last();
    await last.focus();
    await page.keyboard.press('Enter');
    await expect(last).toHaveAttribute('aria-pressed', 'true');
    expect(await years.evaluate(element => element.scrollWidth > element.clientWidth)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
  });

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
    const evidence = dialog.locator('.dossier-evidence');
    await expect(evidence).toBeVisible();
    await expect(evidence.getByRole('link').first()).toBeVisible();
    expect(await dialog.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
    expect(await evidence.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
  });
});
