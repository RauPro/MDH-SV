import { expect, test } from '@playwright/test';
import { formatViolations, runAxe } from './helpers';

// Spec 9: the theme toggle persists the choice across a reload.
test.describe('theme', () => {
  test('defaults to light even when the system prefers dark', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.goto('/');
    await expect(page.getByRole('button', { name: 'Activar tema oscuro' })).toBeVisible();
    await expect(page.locator('html')).toHaveClass('light');
    await expect(page.locator('html')).toHaveCSS('color-scheme', 'light');
  });

  test('restores dark before the application bundle executes', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('mdh_theme', 'dark'));
    await page.route('**/assets/*.js', route => route.abort());
    await page.goto('/');
    await expect(page.locator('html')).toHaveClass('dark', { timeout: 2000 });
  });

  for (const theme of ['light', 'dark']) {
    test(`${theme} keeps the header and editorial layout within the viewport`, async ({ page }, testInfo) => {
      await page.addInitScript(value => localStorage.setItem('mdh_theme', value), theme);
      await page.goto('/');
      await expect(page.locator('.archive-record').first()).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      for (const width of [320, 390, 768, 1440]) {
        await page.setViewportSize({ width, height: 1000 });
        expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
        await expect(page.getByRole('button', { name: 'Datos y API' })).toBeVisible();
        await expect(page.getByRole('button', { name: /Activar tema/ })).toBeVisible();
      }
      await page.screenshot({ path: testInfo.outputPath(`${theme}-desktop.png`) });
    });
  }

  for (const route of ['/', '/#reportes', '/#archivo', '/#estadisticas']) {
    test(`dark theme accessibility at ${route}`, async ({ page }) => {
      await page.addInitScript(() => localStorage.setItem('mdh_theme', 'dark'));
      await page.goto(route);
      await expect(page.getByRole('button', { name: 'Activar tema claro' })).toBeVisible();
      await expect(page.locator('.archive-record, .record, .official-statistics-summary').first()).toBeVisible();
      const violations = await runAxe(page);
      expect(violations, formatViolations(violations)).toEqual([]);
    });
  }

  test('toggle persists across reload', async ({ page }) => {
    await page.goto('/');
    const html = page.locator('html');
    const initial = await html.getAttribute('class');

    const toggle = page.getByRole('button', { name: /Activar tema/ });
    await toggle.click();
    const toggled = await html.getAttribute('class');
    expect(toggled).not.toBe(initial);

    await page.reload();
    await expect(page.locator('.archive-record').first()).toBeVisible();
    expect(await html.getAttribute('class')).toBe(toggled);
  });
});
