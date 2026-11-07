import { expect, test } from '@playwright/test';
import { consoleErrors, dossierBackedReference, externalRequests } from './helpers';

// Spec 8: 390×844 mobile smoke — the home render (spec 1 essentials) and the
// dossier dialog (spec 3) on a phone-sized viewport.
test.describe('mobile smoke', () => {
  test('keeps compact map controls and every department usable at narrow widths', async ({ page }) => {
    await page.goto('/');
    const map = page.getByRole('region', { name: 'Mapa de El Salvador' });
    for (const width of [320, 390, 768]) {
      await page.setViewportSize({ width, height: 844 });
      await expect(map.getByRole('checkbox', { name: 'Homicidios oficiales' })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
      for (const card of await map.locator('.map-departments button').all()) {
        expect(await card.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
        expect((await card.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      }
    }
    const departments = map.getByRole('group', { name: 'Seleccionar departamento en el mapa' }).getByRole('button');
    await expect(departments).toHaveCount(14);
    await departments.first().focus();
    for (const department of await departments.all()) {
      await expect(department).toBeFocused();
      await page.keyboard.press('Enter');
      await expect(department).toHaveAttribute('aria-pressed', 'true');
      await page.keyboard.press('Tab');
    }
    await map.getByText('Cómo leer el mapa y sus fuentes').click();
    await expect(map.getByText(/Cada capa conserva su unidad/)).toBeVisible();
  });

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
